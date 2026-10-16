"""Base entity for the Philips Titan OS TV integration."""

from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TitanOSCoordinator


class TitanOSEntity(CoordinatorEntity[TitanOSCoordinator]):
    """An entity of a Titan OS TV."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TitanOSCoordinator, key: str | None) -> None:
        """Initialize the entity; key is None for the device's main entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry.unique_id
        self._attr_unique_id = (
            entry.unique_id if key is None else f"{entry.unique_id}_{key}"
        )
        system = coordinator.system
        mac = entry.data.get(CONF_MAC)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
            manufacturer="Philips",
            name=entry.title,
            model=system.name,
            model_id=system.model,
            serial_number=system.serial_number,
            sw_version=system.software_version,
        )
