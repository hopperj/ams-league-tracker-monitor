"""Identify service vehicles without relying on observer slots or race position."""
import unicodedata


def is_safety_car(name, car="", car_class=""):
    def label(value):
        if not isinstance(value, str):
            return ""
        return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())

    service_labels = ("safetycar", "safetycarai", "pacecar", "pacecarai")
    return (label(name) in service_labels or label(car_class) in service_labels
            or label(car).endswith(service_labels))
