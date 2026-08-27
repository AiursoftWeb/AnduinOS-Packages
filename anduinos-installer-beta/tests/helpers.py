from dataclasses import replace

from installer_core.model import (
    AccessSpec,
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
from installer_core.swap_policy import GIB, calculate_swap_sizing


TEST_TOPOLOGY_DIGEST = "a" * 64
TEST_INVENTORY_DIGEST = "b" * 64
TEST_PHYSICAL_MEMORY_BYTES = 8 * GIB


def valid_plan(
    *,
    architecture: Architecture = Architecture.AMD64,
    firmware: Firmware = Firmware.UEFI,
    secure_boot: SecureBoot = SecureBoot.ENABLED,
    filesystem: Filesystem = Filesystem.BTRFS,
    install_updates: bool = True,
    install_third_party_drivers: bool = False,
    install_multimedia_codecs: bool = False,
    authentication: AuthenticationMode = AuthenticationMode.PASSWORD,
    sudo_without_password: bool | None = None,
    automatic_login: bool | None = None,
    ssh_password_login: bool = False,
    disk: DiskIdentity | None = None,
    swap_size_mib: int | None = None,
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
            swap_size_mib=(
                calculate_swap_sizing(
                    TEST_PHYSICAL_MEMORY_BYTES,
                    selected_disk.expected_size_bytes,
                ).swap_size_mib
                if swap_size_mib is None
                else swap_size_mib
            ),
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
            password_hash=(
                ""
                if authentication is AuthenticationMode.PASSWORDLESS_SHARED
                else "$y$j9T$example$example"
            ),
        ),
        access=AccessSpec(
            sudo_without_password=(
                authentication is AuthenticationMode.PASSWORDLESS_SHARED
                if sudo_without_password is None
                else sudo_without_password
            ),
            automatic_login=(
                authentication is AuthenticationMode.PASSWORDLESS_SHARED
                if automatic_login is None
                else automatic_login
            ),
            ssh_password_login=ssh_password_login,
        ),
        regional=RegionalSpec(
            locale="en_US.UTF-8",
            timezone="Asia/Singapore",
            keyboard=KeyboardSpec(layout="us"),
        ),
        software=SoftwareSpec(
            install_updates=install_updates,
            install_third_party_drivers=install_third_party_drivers,
            install_multimedia_codecs=install_multimedia_codecs,
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
