"""Validation utility functions."""

import re


def validate_password(password):
    """
    Validate password requirements:
    - must be more than 6 characters
    - must contain at least 1 uppercase letter
    - must contain at least 1 special character
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"
    
    if len(password) <= 6:
        return False, "Password must be more than 6 characters"
    
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least 1 uppercase letter"
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least 1 special character"
    
    return True, None


def validate_email(email):
    """
    Validate email format.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not email:
        return False, "Email is required"
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False, "Invalid email format"
    
    return True, None


def validate_registration_data(data):
    """
    Validate all registration data.
    
    Args:
        data: Dictionary with username, email, address, password, confirm_password
    
    Returns:
        tuple: (is_valid, error_messages_dict)
    """
    errors = {}
    
    # Username (optional, not required by requirements)
    username = data.get('username', '').strip()
    if not username:
        errors['username'] = "Username is required"
    
    # Email
    email = data.get('email', '').strip()
    is_valid, error = validate_email(email)
    if not is_valid:
        errors['email'] = error
    
    # Address
    address = data.get('address', '').strip()
    if not address:
        errors['address'] = "Address is required"
    
    # Password
    password = data.get('password', '')
    is_valid, error = validate_password(password)
    if not is_valid:
        errors['password'] = error
    
    # Confirm Password
    confirm_password = data.get('confirm_password', '')
    if not confirm_password:
        errors['confirm_password'] = "Please confirm your password"
    elif password != confirm_password:
        errors['confirm_password'] = "Passwords do not match"
    
    # Contact number (required for SMS)
    contact_number = data.get('contact_number', '').strip()
    if not contact_number:
        errors['contact_number'] = "Contact number is required for SMS notifications"
    elif not re.match(r'^(09|\+63)\d{9}$', contact_number):
        errors['contact_number'] = "Invalid Philippine phone number format (use 09XXXXXXXXX)"
    
    return len(errors) == 0, errors


def generate_verification_code():
    """Generate a 6-digit verification code."""
    import random
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])
