from dataclasses import replace

from installer_core.model import (
    Architecture,
    AuthenticationMode,
    BootSpec,
    DiskIdentity,
    Filesystem,
    Firmware,
    IdentitySpec,
    InstallMode,
    InstallPlan,
    KeyboardSpec,
    MokPasswordPolicy,
    PlatformSpec,
    RegionalSpec,
    SCHEMA_VERSION,
    SecureBoot,
    SoftwareSpec,
    SourceSpec,
    StorageSpec,
    SwapSpec,
)
from installer_core.storage_graph_planning import build_erase_disk_storage_graph
from installer_core.storage_inventory import (
    DiskInventory,
    DiskTopologyBinding,
    StorageInventory,
)


TEST_TOPOLOGY_DIGEST = "a" * 64
TEST_INVENTORY_DIGEST = "b" * 64


def valid_plan(
    *,
    architecture: Architecture = Architecture.AMD64,
    firmware: Firmware = Firmware.UEFI,
    secure_boot: SecureBoot = SecureBoot.ENABLED,
    filesystem: Filesystem = Filesystem.BTRFS,
    install_updates: bool = True,
    install_third_party_drivers: bool = False,
    authentication: AuthenticationMode = AuthenticationMode.PASSWORD,
    sudo_without_password: bool | None = None,
    disk: DiskIdentity | None = None,
) -> InstallPlan:
    mok_policy = (
        MokPasswordPolicy.ANDUINOS_DEFAULT
        if secure_boot is SecureBoot.ENABLED
        else MokPasswordPolicy.NOT_APPLICABLE
    )
    selected_disk = disk or DiskIdentity(
        path="/dev/nvme0n1",
        stable_id="nvme-Samsung_SSD-test",
        expected_size_bytes=128 * 1024**3,
        model="Samsung Test SSD",
        serial="TEST123",
    )
    plan = InstallPlan(
        schema_version=SCHEMA_VERSION,
        source=SourceSpec(),
        storage=StorageSpec(
            mode=InstallMode.ERASE_DISK,
            disk=selected_disk,
            filesystem=filesystem,
        ),
        platform=PlatformSpec(
            architecture=architecture,
            firmware=firmware,
            secure_boot=secure_boot,
        ),
        identity=IdentitySpec(
            hostname="anduinos",
            username="alice",
            full_name="Alice Example",
            authentication=authentication,
            sudo_without_password=(
                authentication is AuthenticationMode.PASSWORDLESS_SHARED
                if sudo_without_password is None
                else sudo_without_password
            ),
            password_hash=(
                ""
                if authentication is AuthenticationMode.PASSWORDLESS_SHARED
                else "$y$j9T$example$example"
            ),
        ),
        regional=RegionalSpec(
            locale="en_US.UTF-8",
            timezone="Asia/Singapore",
            keyboard=KeyboardSpec(layout="us"),
        ),
        software=SoftwareSpec(
            install_updates=install_updates,
            install_third_party_drivers=install_third_party_drivers,
        ),
        swap=SwapSpec(),
        boot=BootSpec(mok_password_policy=mok_policy),
    )
    graph = build_erase_disk_storage_graph(
        plan,
        DiskTopologyBinding(
            stable_id=plan.storage.disk.stable_id,
            expected_size_bytes=plan.storage.disk.expected_size_bytes,
            topology_digest=TEST_TOPOLOGY_DIGEST,
        ),
        TEST_INVENTORY_DIGEST,
    )
    return replace(plan, storage=replace(plan.storage, graph=graph))


def valid_inventory(
    plan: InstallPlan | None = None,
    *,
    topology_digest: str = TEST_TOPOLOGY_DIGEST,
    path: str | None = None,
) -> StorageInventory:
    selected_plan = plan or valid_plan()
    identity = selected_plan.storage.disk
    if path is not None:
        identity = replace(identity, path=path)
    disk = DiskInventory(
        identity=identity,
        partition_table="gpt",
        partition_table_uuid="test-table",
        partitions=(),
        free_extents=(),
        topology_digest=topology_digest,
    )
    return StorageInventory((disk,), TEST_INVENTORY_DIGEST)
