# 配电保护选择性校核 API

针对配电柜改造场景：更换断路器/熔断器后，校核上下级保护是否仍保持选择性——
下级故障不应越级跳开上级导致整段母线失电，电动机启动不应触发误动作。

技术栈：Python 3.11 · FastAPI · Pydantic v2 · SQLite（标准库 sqlite3）

## 运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
pytest tests/ -q
```

数据库路径用环境变量 `SELECTIVITY_DB` 指定，默认 `./data/selectivity.db`。

## 数据模型

一份工程文档（`ProjectDoc`）包含：

| 部分 | 内容 |
|---|---|
| `units` | 文档级单位声明：电流 `A`/`kA`，时间 `s`/`ms`，导入时统一换算为 A、s |
| `topology` | 单线拓扑：电源点、节点、支路（支路可挂保护设备与负载） |
| `loads` | 静态负载或电动机（启动电流、启动历时） |
| `fault_currents` | 各母线节点的故障电流范围 `[min, max]` |
| `devices` | 断路器/熔断器：额定电流、多个整定档位、当前档位；每档含过载/短路/瞬时分段曲线（电流—时间点列）与电流/时间容差 |

## 导入校验（`POST /projects/import`）

全部通过才入库，否则返回 422 及完整错误列表：

- 未知单位；
- **非单调曲线**：电流点必须严格递增，动作时间必须单调不增（允许瞬时段水平线），时间回升即拒绝；
- **断开的拓扑**：任何节点不可达电源点即拒绝；
- **缺失保护段**：每个档位必须含 `overload` 与 `short_circuit` 段（`instantaneous` 可选，见 `app/rules.py`）；
- 电动机缺启动参数、故障电流范围颠倒、引用悬空、非源节点缺故障电流等。

## 校核引擎（`POST /checks`）

- 分段曲线在**对数坐标**下线性插值，结合容差生成动作时间带
  `t_lo(I)`（最快）/ `t_hi(I)`（最慢）；
- 沿每条馈线向电源方向取**上下级链**，对相邻两级逐段扫描：
  - 过载段：`[负载电流, 故障电流上限]`
  - 短路/瞬时段：`[故障电流下限, 上限]`
  - 判定 `t_上级,lo(I) − t_下级,hi(I) ≥ 分级裕量`（裕量见 `app/rules.py`，对数采样 + 二分定位边界）；
- 电动机启动包络：链上每级在启动电流处的最快动作时间必须大于启动历时 + 裕量；
- 每条冲突给出：**曲线重叠的电流区间**、**最小时间裕量**及其处电流、**受影响回路/设备**；
- 每次校核保存快照：输入文档、规则版本（`RULES_VERSION`）、判定明细。

## 沙盒试算（`POST /sandbox`）

在文档副本上枚举指定设备的允许档位组合（上限 4096，见 `rules.py`），
**不修改基线**。返回：

- `feasible`：零冲突的可行整定组合；
- `remaining_conflicts`：无可行解时按冲突数/最差裕量排序的最接近方案及其残余冲突。

## 快照与风险对比

- `GET /snapshots` / `GET /snapshots/{id}`：快照列表与明细；
- `GET /snapshots/compare?a=1&b=2`：以 （下级， 上级， 类型） 为键对比两次快照，
  给出新增冲突、已消除冲突、最小裕量变化与风险净变化，并提示规则版本是否一致。

## 目录

```
app/
  main.py       FastAPI 路由
  schemas.py    Pydantic 输入模型
  units.py      单位换算（A / s 基准）
  importer.py   导入校验与归一化
  curves.py     单调性校验、对数插值、容差时间带
  topology.py   连通性校验、上下级链
  analysis.py   选择性校核引擎
  sandbox.py    档位组合搜索
  compare.py    快照风险对比
  rules.py      规则版本、分级裕量、采样参数
  db.py         SQLite 持久化
tests/          pytest 端到端测试
```
