import secrets
import string

from django.contrib.auth import get_user_model
from django.db import transaction

from .audit import audit
from .models import Profile

User = get_user_model()


def temp_password(n=12):
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(n - 2)) + secrets.choice("!@#$%") + secrets.choice("23456789")
        if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p):
            return p


@transaction.atomic
def create_staff(username, first_name, last_name, role, email="", phone="", password=None, created_by=None):
    pw = password or temp_password()
    user = User.objects.create_user(username=username.lower(), email=email, password=pw,
                                    first_name=first_name, last_name=last_name)
    Profile.objects.create(user=user, role=role, phone=phone, must_change_password=True)
    audit("user.create", "security", "User", user.pk, username, new={"role": role, "email": email},
          detail=f"Staff account created ({role})", user=created_by)
    return user, pw
