"""时间—电流曲线：单调性校验、对数插值、容差时间带。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import Segment


class CurveError(ValueError):
    pass


def validate_segment_curve(device_id: str, setting: str, seg: Segment) -> None:
    """电流必须严格递增，动作时间必须单调不增（允许瞬时段的水平线）。
    任何电流下降或时间回升都视为非单调曲线，拒绝导入。"""
    pts = seg.points
    for prev, cur in zip(pts, pts[1:]):
        if cur.i <= prev.i:
            raise CurveError(
                f"设备 {device_id} 档位 {setting} 段 {seg.kind}: "
                f"电流点非严格递增 ({prev.i} -> {cur.i})"
            )
        if cur.t > prev.t * (1 + 1e-9):
            raise CurveError(
                f"设备 {device_id} 档位 {setting} 段 {seg.kind}: "
                f"动作时间随电流回升，曲线非单调 (I={cur.i}: t {prev.t} -> {cur.t})"
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

    def t_lo(self, current: float) -> float:
        """最快动作边界：电流正偏差 + 时间负偏差。"""
        i = current * (1.0 + self.cur_tol)
        return self._interp(i) * (1.0 - self.time_tol)

    def t_hi(self, current: float) -> float:
        """最慢动作边界：电流负偏差 + 时间正偏差。"""
        i = current * (1.0 - self.cur_tol)
        return self._interp(i) * (1.0 + self.time_tol)
