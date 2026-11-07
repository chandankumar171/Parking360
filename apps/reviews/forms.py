from django import forms


class ReviewForm(forms.Form):
    rating = forms.TypedChoiceField(
        choices=[(i, str(i)) for i in range(5, 0, -1)], coerce=int, widget=forms.RadioSelect,
        error_messages={"required": "Choose a star rating."})
    comment = forms.CharField(
        required=False, max_length=1000, label="Your review",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "How was the parking? Safety, space, ease of entry, staff..."}))


class ReplyForm(forms.Form):
    text = forms.CharField(min_length=2, max_length=600)
