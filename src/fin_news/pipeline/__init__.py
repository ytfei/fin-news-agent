"""Pipeline：事件驱动的评分 / 向量化 / 深度分析编排。"""
from fin_news.pipeline.worker import PipelineWorker, run_worker_forever

__all__ = ["PipelineWorker", "run_worker_forever"]
