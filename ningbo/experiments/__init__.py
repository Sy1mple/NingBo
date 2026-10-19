"""Only the module registration list; implementations are scene-owned."""
EXPERIMENTS = ("environment", "body_temperature", "ultrasonic", "smoke", "fan", "access", "servo", "stepper", "voice")
HARDWARE_MODULES = ('experiments.environment.jetson.controller', 'experiments.body_temperature.jetson.controller', 'experiments.ultrasonic.jetson.controller', 'experiments.smoke.jetson.controller', 'experiments.fan.jetson.controller', 'experiments.access.jetson.controller', 'experiments.servo.jetson.controller', 'experiments.stepper.jetson.controller', 'hardware.rgb.controller')
