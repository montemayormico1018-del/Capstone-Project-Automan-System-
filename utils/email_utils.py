"""Email Utility Functions - For sending OTP and notifications."""

import os
import smtplib
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from flask import current_app


def generate_otp(length=6):
    """Generate a random OTP code."""
    return ''.join(random.choices(string.digits, k=length))


def _get_email_credentials():
    """Read the sending account from the environment (see .env).

    Returns (sender_email, sender_password) or (None, None) if not configured.
    """
    sender_email = os.environ.get('EMAIL_ADDRESS')
    sender_password = os.environ.get('EMAIL_PASSWORD')
    if not sender_email or not sender_password:
        return None, None
    return sender_email, sender_password


def send_otp_email(recipient_email, otp_code, username=None):
    """
    Send OTP verification email to customer.

    Args:
        recipient_email: Customer's email address
        otp_code: 6-digit OTP code
        username: Customer's username (optional)

    Returns:
        dict: Result with status
    """
    sender_email, sender_password = _get_email_credentials()
    if not sender_email or not sender_password:
        message = 'EMAIL_ADDRESS/EMAIL_PASSWORD are not set in .env'
        current_app.logger.error('send_otp_email: %s', message)
        return {'status': 'error', 'message': message}

    subject = 'Your Automan Car Care Verification Code'
    
    username_display = username or 'Customer'
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }}
            .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .header h1 {{ color: #0f0f10; margin: 0; }}
            .content {{ text-align: center; }}
            .otp-code {{ font-size: 42px; font-weight: bold; color: #e53935; letter-spacing: 10px; margin: 30px 0; }}
            .footer {{ text-align: center; margin-top: 30px; color: #888; font-size: 12px; }}
            .note {{ color: #666; font-size: 14px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Automan Car Care</h1>
            </div>
            <div class="content">
                <p>Hello, <strong>{username_display}</strong>!</p>
                <p>Your verification code is:</p>
                <div class="otp-code">{otp_code}</div>
                <p class="note">This code will expire in 10 minutes.</p>
                <p>If you didn't request this code, please ignore this email.</p>
            </div>
            <div class="footer">
                <p>Automan Car Care Center<br>
                Your Trusted Vehicle Service Partner</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_body = f"""
    Hello, {username_display}!
    
    Your verification code is: {otp_code}
    
    This code will expire in 10 minutes.
    
    If you didn't request this code, please ignore this email.
    
    Automan Car Care Center
    Your Trusted Vehicle Service Partner
    """
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Automan Car Care <{sender_email}>"
        msg['To'] = recipient_email
        
        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')
        
        msg.attach(part1)
        msg.attach(part2)
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        
        return {'status': 'success', 'message': 'OTP sent successfully'}

    except Exception as e:
        current_app.logger.exception('send_otp_email: sending failed: %s', e)
        return {'status': 'error', 'message': f'Failed to send email: {e}'}


def send_password_reset_email(recipient_email, reset_link, username=None):
    """
    Send a password-reset link to a user who requested one via "Forgot password".

    Args:
        recipient_email: The account's email address
        reset_link: Full URL to the reset-password page, including the token
        username: Account's username (optional)

    Returns:
        dict: Result with status
    """
    sender_email, sender_password = _get_email_credentials()
    if not sender_email or not sender_password:
        message = 'EMAIL_ADDRESS/EMAIL_PASSWORD are not set in .env'
        current_app.logger.error('send_password_reset_email: %s', message)
        return {'status': 'error', 'message': message}

    subject = 'Reset Your Automan Car Care Password'

    username_display = username or 'there'

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }}
            .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .header h1 {{ color: #0f0f10; margin: 0; }}
            .content {{ color: #333; }}
            .btn {{ display: inline-block; padding: 12px 30px; background: #e53935; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .note {{ color: #666; font-size: 14px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 30px; color: #888; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Automan Car Care</h1>
            </div>
            <div class="content">
                <p>Hello, <strong>{username_display}</strong>!</p>
                <p>We received a request to reset your password. Click the button below to choose a new one:</p>
                <p><a href="{reset_link}" class="btn">Reset Password</a></p>
                <p class="note">This link will expire in 30 minutes. If you didn't request a password reset, you can safely ignore this email &mdash; your password will not be changed.</p>
            </div>
            <div class="footer">
                <p>Automan Car Care Center<br>
                Your Trusted Vehicle Service Partner</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_body = f"""
    Hello, {username_display}!

    We received a request to reset your password. Open the link below to choose a new one:

    {reset_link}

    This link will expire in 30 minutes.

    If you didn't request a password reset, you can safely ignore this email -- your password will not be changed.

    Automan Car Care Center
    Your Trusted Vehicle Service Partner
    """

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Automan Car Care <{sender_email}>"
        msg['To'] = recipient_email

        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')

        msg.attach(part1)
        msg.attach(part2)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()

        return {'status': 'success', 'message': 'Password reset email sent'}

    except Exception as e:
        current_app.logger.exception('send_password_reset_email: sending failed: %s', e)
        return {'status': 'error', 'message': f'Failed to send email: {e}'}


def send_welcome_email(recipient_email, username):
    """Send welcome email after successful verification."""
    sender_email, sender_password = _get_email_credentials()
    if not sender_email or not sender_password:
        message = 'EMAIL_ADDRESS/EMAIL_PASSWORD are not set in .env'
        current_app.logger.error('send_welcome_email: %s', message)
        return {'status': 'error', 'message': message}

    subject = 'Welcome to Automan Car Care!'
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }}
            .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .header h1 {{ color: #0f0f10; margin: 0; }}
            .content {{ color: #333; }}
            .features {{ margin: 20px 0; }}
            .features li {{ margin: 10px 0; }}
            .btn {{ display: inline-block; padding: 12px 30px; background: #e53935; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }}
            .footer {{ text-align: center; margin-top: 30px; color: #888; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Automan Car Care</h1>
            </div>
            <div class="content">
                <p>Hello, <strong>{username}</strong>!</p>
                <p>Welcome to Automan Car Care Center! Your account has been successfully verified.</p>
                <div class="features">
                    <h3>What you can do:</h3>
                    <ul>
                        <li>Book appointments for your vehicle</li>
                        <li>Track your service status</li>
                        <li>View service history</li>
                        <li>Receive notifications</li>
                    </ul>
                </div>
                <p><a href="http://localhost:5000/login" class="btn">Login Now</a></p>
            </div>
            <div class="footer">
                <p>Automan Car Care Center<br>
                Your Trusted Vehicle Service Partner</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    text_body = f"""
    Hello, {username}!
    
    Welcome to Automan Car Care Center! Your account has been successfully verified.
    
    What you can do:
    - Book appointments for your vehicle
    - Track your service status
    - View service history
    - Receive notifications
    
    Login now: http://localhost:5000/login
    
    Automan Car Care Center
    Your Trusted Vehicle Service Partner
    """
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Automan Car Care <{sender_email}>"
        msg['To'] = recipient_email
        
        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')
        
        msg.attach(part1)
        msg.attach(part2)
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        
        return {'status': 'success'}

    except Exception as e:
        current_app.logger.exception('send_welcome_email: sending failed: %s', e)
        return {'status': 'error', 'message': f'Failed to send email: {e}'}