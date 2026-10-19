"""Registry only. All button/VP definitions and decoding live in their own scene."""
from importlib import import_module
from experiments import EXPERIMENTS
from system.diagnostics.serial_screen import addresses as _diagnostics
from system.diagnostics.serial_screen.screen import configured_status_button_events, validate_status_result_addresses

SCENE_ADDRESS_MODULES = tuple(import_module(f"experiments.{name}.serial_screen.addresses") for name in EXPERIMENTS)
EVENT_MODULES = tuple(import_module(f"experiments.{name}.serial_screen.screen") for name in EXPERIMENTS)
ADDRESS_MAP, BUTTON_EVENT_MAP, INPUT_EVENT_MAP = {}, {}, {}
for module in SCENE_ADDRESS_MODULES:
    for name in ("ADDRESS_MAP", "BUTTON_EVENT_MAP", "INPUT_EVENT_MAP"):
        target, incoming = globals()[name], getattr(module, name)
        if set(target) & set(incoming):
            raise ValueError("Duplicate screen registration: " + module.__name__)
        target.update(incoming)
BUTTON_EVENT_MAP.update(configured_status_button_events())
validate_status_result_addresses()


def __getattr__(name):
    # Named constants remain importable for tools; the source of truth is local.
    for module in (*SCENE_ADDRESS_MODULES, _diagnostics):
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)


def decode_event(address, value, frame=b""):
    for module in EVENT_MODULES:
        result = module.decode_event(address, value, frame)
        if result is not None:
            return result
    return {"event": BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address, "screen.unknown")),
            "address": address, "value": value, "hex": frame.hex(" ").upper()}
