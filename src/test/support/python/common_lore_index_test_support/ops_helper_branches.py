from common_lore_index_test_support.ops_helper_branches_baselines import (
    OpsBaselineHelperBranchContract,
)
from common_lore_index_test_support.ops_helper_branches_preflight_config_mcp import (
    OpsPreflightConfigMcpHelperBranchContract,
)
from common_lore_index_test_support.ops_helper_branches_runtime_backup_mcp import (
    OpsRuntimeBackupMcpHelperBranchContract,
)


class OpsHelperBranchContractTests(
    OpsBaselineHelperBranchContract,
    OpsPreflightConfigMcpHelperBranchContract,
    OpsRuntimeBackupMcpHelperBranchContract,
):
    __test__ = False

    pass
