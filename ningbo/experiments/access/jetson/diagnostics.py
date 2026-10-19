"""access 场景的执行器状态检查。"""
from hardware.diagnostics import ActuatorCheck


class LockStatusCheck(ActuatorCheck):
    def expected_levels(self, state, command_on):
        config = self.manager.config.lock
        return {config.pin: int(command_on == config.active_high)}
