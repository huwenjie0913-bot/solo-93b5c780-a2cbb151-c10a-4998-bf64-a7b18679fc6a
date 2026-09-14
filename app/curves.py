"""时间—电流曲线：单调性校验、对数插值、容差时间带。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import ProtectedDevice, Segment


class CurveError(ValueError):
    pass


def validate_monotonic_points(points, label: str) -> None:
    """通用校验：电流点严格递增，时间随电流单调不增（允许水平段）。
    任何电流下降或时间回升都视为非单调曲线。label 用于定位错误来源。"""
    for prev, cur in zip(points, points[1:]):
        if cur.i <= prev.i:
            raise CurveError(
                f"{label}: 电流点非严格递增 ({prev.i} -> {cur.i})"
            )
        if cur.t > prev.t * (1 + 1e-9):
            raise CurveError(
                f"{label}: 允许时间随电流回升，曲线非单调 (I={cur.i}: t {prev.t} -> {cur.t})"
            )


def validate_segment_curve(device_id: str, setting: str, seg: Segment) -> None:
    """电流必须严格递增，动作时间必须单调不增（允许瞬时段的水平线）。
    任何电流下降或时间回升都视为非单调曲线，拒绝导入。"""
    validate_monotonic_points(
        seg.points, f"设备 {device_id} 档位 {setting} 段 {seg.kind}"
    )


@dataclass
class TimeBand:
    """一条保护段的动作时间带：t_lo(I) / t_hi(I)，对数坐标线性插值。"""

    log_i: list[float]
    log_t: list[float]
    cur_tol: float
    time_tol: float

    @classmethod
    def from_segment(cls, seg: Segment) -> "TimeBand":
        return cls(
            log_i=[math.log(p.i) for p in seg.points],
            log_t=[math.log(p.t) for p in seg.points],
            cur_tol=seg.tolerance.current_pct,
            time_tol=seg.tolerance.time_pct,
        )

    def _interp(self, current: float) -> float:
        """对数插值求名义动作时间；超出曲线范围时取端点值（不外推）。"""
        x = math.log(current)
        xs, ys = self.log_i, self.log_t
        if x <= xs[0]:
            return math.exp(ys[0])
        if x >= xs[-1]:
            return math.exp(ys[-1])
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        w = (x - xs[lo]) / (xs[lo + 1] - xs[lo])
        return math.exp(ys[lo] + w * (ys[lo + 1] - ys[lo]))

    @property
    def i_domain(self) -> tuple[float, float]:
        return math.exp(self.log_i[0]), math.exp(self.log_i[-1])

    def covers_current(self, current: float) -> bool:
        """最慢边界取点（电流负偏差）仍在录入点范围内，才算该段覆盖此电流。
        热耐受校核用：无任何段覆盖的故障区间判为未判定，不做端点钳制。"""
        i = current * (1.0 - self.cur_tol)
        lo, hi = self.i_domain
        return lo <= i <= hi

    def t_lo(self, current: float) -> float:
        """最快动作边界：电流正偏差 + 时间负偏差。"""
        i = current * (1.0 + self.cur_tol)
        return self._interp(i) * (1.0 - self.time_tol)

    def t_hi(self, current: float) -> float:
        """最慢动作边界：电流负偏差 + 时间正偏差。"""
        i = current * (1.0 - self.cur_tol)
        return self._interp(i) * (1.0 + self.time_tol)


@dataclass
class WithstandBand:
    """受保护设备（电缆/变压器等）的损伤曲线时间带。

    损伤曲线只在录入电流点范围内有效；范围之外不做端点钳制外推，
    由校核引擎将该部分故障区间判为“未判定”，避免把外推结果当安全。
    """

    log_i: list[float]
    log_t: list[float]
    cur_tol: float
    time_tol: float

    @classmethod
    def from_device(cls, dev: ProtectedDevice) -> "WithstandBand":
        return cls(
            log_i=[math.log(p.i) for p in dev.damage_curve],
            log_t=[math.log(p.t) for p in dev.damage_curve],
            cur_tol=dev.tolerance.current_pct,
            time_tol=dev.tolerance.time_pct,
        )

    @property
    def i_min(self) -> float:
        return math.exp(self.log_i[0])

    @property
    def i_max(self) -> float:
        return math.exp(self.log_i[-1])

    def covers(self, current: float) -> bool:
        """电流偏差后的最保守取点仍在录入曲线范围内才算覆盖（不外推）。"""
        return current * (1.0 - self.cur_tol) >= self.i_min and \
            current * (1.0 + self.cur_tol) <= self.i_max

    def _interp_strict(self, current: float) -> float:
        """对数插值，仅在录入点范围内有定义；越界抛 LookupError。"""
        x = math.log(current)
        xs, ys = self.log_i, self.log_t
        if x < xs[0] or x > xs[-1]:
            raise LookupError("耐受曲线外无定义，禁止外推")
        if x == xs[0]:
            return math.exp(ys[0])
        if x == xs[-1]:
            return math.exp(ys[-1])
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        w = (x - xs[lo]) / (xs[lo + 1] - xs[lo])
        return math.exp(ys[lo] + w * (ys[lo + 1] - ys[lo]))

    def t_conservative(self, current: float) -> float:
        """最保守耐受边界：电流偏大（伤害更早）+ 允许时间负偏差。
        偏差取点超出录入范围时抛 LookupError，调用方应判为未判定区间。"""
        i = current * (1.0 + self.cur_tol)
        t = self._interp_strict(i)
        return t * (1.0 - self.time_tol)
