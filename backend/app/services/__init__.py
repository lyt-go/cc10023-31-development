"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.feature_source import FeatureSourceService
from app.services.feature_define import FeatureDefineService
from app.services.source_mapping import SourceMappingService
from app.services.feature_group import FeatureGroupService
from app.services.data_slice import DataSliceService
from app.services.feature_compute import FeatureComputeService
from app.services.feature_selection import FeatureSelectionService
from app.services.feature_iv import FeatureIvService
from app.services.feature_snapshot import FeatureSnapshotService
from app.services.train_pipeline import TrainPipelineService
from app.services.model_registry import ModelRegistryService
from app.services.predict_batch import PredictBatchService
from app.services.mining_result import MiningResultService
from app.services.cluster_config import ClusterConfigService
from app.services.feature_corr import FeatureCorrService
from app.services.drift_monitor import DriftMonitorService
from app.services.data_export import DataExportService
from app.services.pipeline_schedule import PipelineScheduleService
from app.services.scenario_label import ScenarioLabelService

SERVICES: dict[str, BaseModuleService] = {
    "feature_source": FeatureSourceService(),
    "feature_define": FeatureDefineService(),
    "source_mapping": SourceMappingService(),
    "feature_group": FeatureGroupService(),
    "data_slice": DataSliceService(),
    "feature_compute": FeatureComputeService(),
    "feature_selection": FeatureSelectionService(),
    "feature_iv": FeatureIvService(),
    "feature_snapshot": FeatureSnapshotService(),
    "train_pipeline": TrainPipelineService(),
    "model_registry": ModelRegistryService(),
    "predict_batch": PredictBatchService(),
    "mining_result": MiningResultService(),
    "cluster_config": ClusterConfigService(),
    "feature_corr": FeatureCorrService(),
    "drift_monitor": DriftMonitorService(),
    "data_export": DataExportService(),
    "pipeline_schedule": PipelineScheduleService(),
    "scenario_label": ScenarioLabelService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
