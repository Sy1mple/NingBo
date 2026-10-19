"""Ultrasonic RGB distance thresholds, independent of buzzer output."""
from .readings import valid_number


def color_for(state, config, now):
    policy = config.rgb_feedback
    value = valid_number(state, "distance", now, policy.sample_max_age_seconds,
                         0, config.ultrasonic.maximum_distance_cm)
    if value is None:
        return policy.waiting_color
    if value < policy.distance_danger_cm:
        return "red"
    if value < policy.distance_caution_cm:
        return policy.waiting_color
    return "green"
