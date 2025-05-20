from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.contrib import messages # Optional: for messages if access denied

def admin_required(function=None, redirect_field_name=None, login_url='login'):
    """
    Decorator for views that checks that the logged in user is an Admin.
    """
    actual_decorator = user_passes_test(
        lambda u: u.is_active and u.is_admin, # Using the is_admin property from your User model
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    if function:
        return actual_decorator(function)
    return actual_decorator

def viewer_or_admin_required(function=None, redirect_field_name=None, login_url='login'):
    """
    Decorator for views that checks that the logged in user is either a Viewer or an Admin.
    """
    actual_decorator = user_passes_test(
        lambda u: u.is_active and (u.is_viewer or u.is_admin), # Using properties from User model
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    if function:
        return actual_decorator(function)
    return actual_decorator

# Alternative/More explicit if you prefer checking the 'role' field directly:
# def admin_required_explicit(function=None, redirect_field_name=None, login_url='login'):
#     def check_admin(user):
#         if not user.is_authenticated:
#             return False
#         if user.role == 'Admin':
#             return True
#         messages.error(user.request, "You do not have permission to access this page.") # Hypothetical if messages are used this way
#         return False
#     actual_decorator = user_passes_test(check_admin, login_url=login_url, redirect_field_name=redirect_field_name)
#     if function:
#         return actual_decorator(function)
#     return actual_decorator