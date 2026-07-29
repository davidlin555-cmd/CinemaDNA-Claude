"""OptimizerDNA (Phase G) —— 自我优化子模型（复盘脑）。

从工厂自己的遥测里学习，越跑越好；且优化本身也要被验收（变差就回滚）。
"""

from .profile import TuningProfile
from .analyzer import Recommendation, analyze, hotspot_report
from .service import FactoryOptimizer, OptimizationReport

__all__ = [
    "TuningProfile", "Recommendation", "analyze", "hotspot_report",
    "FactoryOptimizer", "OptimizationReport",
]
