"""stepper: the authoritative VP and button definitions for this screen page."""

# Match the paired HMI project; six bytes occupy only 0x1300~0x1302.
STEPPER_DIRECTION = 0x1300

STEPPER_NUMBER = 0x1063

STEPPER_CONTROL_START_KEY_ADDR = 0x3060

STEPPER_CONTROL_START_BACK_VALUE = 0x5060

STEPPER_CONTROL_STOP_KEY_ADDR = 0x3070

STEPPER_CONTROL_STOP_BACK_VALUE = 0x5070

ADDRESS_MAP = {
    'stepper.direction': STEPPER_DIRECTION,
    'stepper.steps': STEPPER_NUMBER,
}

BUTTON_EVENT_MAP = {
    (STEPPER_CONTROL_START_KEY_ADDR, STEPPER_CONTROL_START_BACK_VALUE): 'stepper.start',
    (STEPPER_CONTROL_STOP_KEY_ADDR, STEPPER_CONTROL_STOP_BACK_VALUE): 'stepper.stop',
}

# 这两个 VP 用于显示；写回/回显不得当成下一次电机控制参数。
INPUT_EVENT_MAP = {}

# Status result VP owned by this module.
STEPPER_STATUS_RESULT_ADDR = 0x6006
