# Run this in Python to generate a new secret key:
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())