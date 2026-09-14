"""校核规则版本与分级裕量。修改规则时必须升级 RULES_VERSION。"""

RULES_VERSION = "1.0.0"

# 每套整定必须包含的保护段；缺失即在导入时拒绝
REQUIRED_SEGMENTS = ("overload", "short_circuit")
OPTIONAL_SEGMENTS = ("instantaneous",)

# 上下级之间要求的最小动作时间差（秒）
GRADING_MARGINS_S = {
    "overload": 0.20,
    "short_circuit": 0.10,
    "instantaneous": 0.05,
    "motor_start": 0.10,
}

# 电流区间采样点数（对数等距）
SAMPLE_POINTS = 200

# 沙盒组合搜索上限
MAX_SANDBOX_COMBINATIONS = 4096
