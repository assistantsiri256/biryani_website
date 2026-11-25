from django.contrib.auth.decorators import user_passes_test


def is_company_admin(user):
    return user.is_authenticated and (user.is_superuser or user.role == "admin")


admin_required = user_passes_test(is_company_admin, login_url="accounts:login")
