"""Body-temperature RGB rule; shared arbitration chooses the final physical color."""
from .readings import valid_number


def color_for(state, config, now):
    policy = config.rgb_feedback
    low, high = policy.body_temperature_valid_range
    value = valid_number(state, "temperature", now, policy.sample_max_age_seconds, low, high)
    return policy.waiting_color if value is None else "red" if value > policy.body_temperature_high_c else "green"
