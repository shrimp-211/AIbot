"""管道系统测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.scheduler import PipelineScheduler
from pipeline.stages import (
    WakeCheckStage, RateLimitStage, ContentSafetyStage,
    PreProcessStage, ProcessStage, DecorateStage, RespondStage,
)


def test_pipeline_imports():
    """验证所有7个阶段和调度器可导入."""
    assert PipelineScheduler is not None
    stages = [WakeCheckStage, RateLimitStage, ContentSafetyStage,
              PreProcessStage, ProcessStage, DecorateStage, RespondStage]
    assert len(stages) == 7


def test_scheduler_build():
    """验证调度器构建和阶段添加."""
    from security.auth import AuthManager
    auth = AuthManager()

    class FakeCfg:
        def get(self, k, d=None):
            return d

    cfg = FakeCfg()
    scheduler = PipelineScheduler()
    scheduler.add_stage(WakeCheckStage(auth=auth, cfg=cfg))
    scheduler.add_stage(RateLimitStage(cfg=cfg))
    scheduler.add_stage(ContentSafetyStage(cfg=cfg))
    scheduler.add_stage(PreProcessStage(cfg=cfg))
    scheduler.add_stage(DecorateStage(cfg=cfg))
    scheduler.add_stage(RespondStage())
    assert len(scheduler._stages) == 6


if __name__ == "__main__":
    test_pipeline_imports(); print("test_pipeline_imports: OK")
    test_scheduler_build(); print("test_scheduler_build: OK")
    print("All pipeline tests passed")
