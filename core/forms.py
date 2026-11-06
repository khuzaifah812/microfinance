from django import forms


class StyledMixin:
    """Adds CSS classes to widgets and a consistent look to every form."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "chk"
            elif isinstance(w, (forms.FileInput, forms.ClearableFileInput)):
                w.attrs["class"] = "file"
            else:
                w.attrs["class"] = (w.attrs.get("class", "") + " inp").strip()
            if isinstance(w, forms.DateInput):
                w.input_type = "date"
                w.format = "%Y-%m-%d"
            if f.required and not isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("required", "required")


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        kw.setdefault("format", "%Y-%m-%d")
        super().__init__(**kw)


def money_field(label, **kw):
    kw.setdefault("min_value", 1)
    return forms.DecimalField(label=label, max_digits=14, decimal_places=0, widget=forms.NumberInput(attrs={"step": "1", "inputmode": "numeric"}), **kw)
