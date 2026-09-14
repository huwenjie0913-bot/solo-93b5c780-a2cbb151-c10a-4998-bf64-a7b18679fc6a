"""单位统一：导入时把所有电流换算到安培、时间换算到秒。"""

CURRENT_TO_A = {"A": 1.0, "kA": 1e3}
TIME_TO_S = {"s": 1.0, "ms": 1e-3}


class UnitError(ValueError):
    pass


def current_factor(unit: str) -> float:
    try:
        return CURRENT_TO_A[unit]
    except KeyError:
        raise UnitError(f"未知电流单位 {unit!r}，支持: {sorted(CURRENT_TO_A)}")


def time_factor(unit: str) -> float:
    try:
        return TIME_TO_S[unit]
    except KeyError:
        raise UnitError(f"未知时间单位 {unit!r}，支持: {sorted(TIME_TO_S)}")
