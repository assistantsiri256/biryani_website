from django import forms

from .models import SupportTicket


class StyledMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "form-check-input")
            else:
                f.widget.attrs.setdefault("class", "form-select" if isinstance(f.widget, forms.Select) else "form-control")


class TicketForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ["category", "subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}


class ReplyForm(StyledMixin, forms.Form):
    reply = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), max_length=3000)
    close = forms.BooleanField(required=False, label="Close ticket after replying")
