import os
from datetime import timedelta

class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'automan-car-care-secret-key-2024'

    # JWT Configuration
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'automan-jwt-secret-key-2024-automan-123456'
    JWT_IDENTITY_CLAIM = 'identity'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
    JWT_TOKEN_LOCATION = ['headers']
    JWT_HEADER_NAME = 'Authorization'
    JWT_HEADER_TYPE = 'Bearer'

    # Maximum customer appointments allowed per date+time slot before it's
    # considered fully booked. Admin/staff bookings are exempt — see
    # create_appointment() in routes/appointments.py.
    MAX_BOOKINGS_PER_SLOT = int(os.environ.get('MAX_BOOKINGS_PER_SLOT', 3))


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
