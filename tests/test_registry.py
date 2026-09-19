import importlib

import pytest

from app.equipment.base import Equipment
from app.equipment.compressor import GasCompressor
from app.equipment.registry import EquipmentRegistry


DEVICE_MODULES = (
    "app.equipment.compressor",
    "app.equipment.pump",
)


def _registered_classes():
    for name in DEVICE_MODULES:
        importlib.import_module(name)

    return sorted(
        Equipment.registered().values(),
        key=lambda device_class: device_class.__name__,
    )


def _device_ids(device_class):
    return device_class.__name__


REGISTERED = _registered_classes()


def test_contract_devices_are_available_to_round_trip():
    assert GasCompressor in REGISTERED


@pytest.mark.parametrize("device_class", REGISTERED, ids=_device_ids)
def test_registry_round_trips_every_built_in_type(device_class):
    registry = EquipmentRegistry()
    device = device_class()

    registry.register(device)

    assert registry.resolve(device.tag) is device


def test_duplicate_tag_registration_raises():
    registry = EquipmentRegistry()
    registry.register(GasCompressor())

    with pytest.raises(ValueError):
        registry.register(GasCompressor())


def test_unknown_tag_lookup_raises_with_a_useful_message():
    registry = EquipmentRegistry()
    registry.register(GasCompressor())

    with pytest.raises(KeyError) as excinfo:
        registry.resolve("FV-999")

    message = str(excinfo.value)

    assert "FV-999" in message
    assert "K-101" in message


def test_register_returns_the_device():
    registry = EquipmentRegistry()
    compressor = GasCompressor()

    assert registry.register(compressor) is compressor
