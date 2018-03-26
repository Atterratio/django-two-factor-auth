from __future__ import absolute_import, division, unicode_literals

import logging
from binascii import unhexlify

from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _
from django_otp.models import Device
from django_otp.oath import totp
from django_otp.util import hex_validator, random_hex
from phonenumber_field.modelfields import PhoneNumberField

from .gateways import make_call, send_email, send_sms

try:
    import yubiotp
except ImportError:
    yubiotp = None


logger = logging.getLogger(__name__)

PHONE_METHODS = (
    ('call', _('Phone Call')),
    ('sms', _('Text Message')),
)


def get_available_email_methods():
    methods = []
    if getattr(settings, 'TWO_FACTOR_EMAIL_ALLOW', True)\
            and getattr(settings, 'EMAIL_BACKEND', None)\
            and getattr(settings, 'DEFAULT_FROM_EMAIL', None):
        methods.append(('email', _('Email message')))
    return methods


def get_available_phone_methods():
    methods = []
    if getattr(settings, 'TWO_FACTOR_CALL_GATEWAY', None):
        methods.append(('call', _('Phone call')))
    if getattr(settings, 'TWO_FACTOR_SMS_GATEWAY', None):
        methods.append(('sms', _('Text message')))
    return methods


def get_available_yubikey_methods():
    methods = []
    if yubiotp and 'otp_yubikey' in settings.INSTALLED_APPS:
        methods.append(('yubikey', _('YubiKey')))
    return methods


def get_available_methods():
    methods = [('generator', _('Token generator'))]
    methods.extend(get_available_email_methods())
    methods.extend(get_available_phone_methods())
    methods.extend(get_available_yubikey_methods())
    return methods


def key_validator(*args, **kwargs):
    """Wraps hex_validator generator, to keep makemigrations happy."""
    return hex_validator()(*args, **kwargs)


class TFADevice(Device):
    class Meta:
        app_label = 'two_factor'
        abstract = True

    drift_range = []
    key = models.CharField(max_length=40,
                           validators=[key_validator],
                           default=random_hex,
                           help_text="Hex-encoded secret key")

    @property
    def bin_key(self):
        return unhexlify(self.key.encode())

    def verify_token(self, token):
        # local import to avoid circular import
        from two_factor.utils import totp_digits

        try:
            token = int(token)
        except ValueError:
            return False

        for drift in self.drift_range:
            if totp(self.bin_key, drift=drift, digits=totp_digits()) == token:
                return True
        return False

    def generate_challenge(self):
        # local import to avoid circular import
        from two_factor.utils import totp_digits

        """
        Sends the current TOTP token to `self.number` using `self.method`.
        """
        no_digits = totp_digits()
        token = str(totp(self.bin_key, digits=no_digits)).zfill(no_digits)
        if isinstance(self, EmailDevice):
            send_email(device=self, token=token)
        elif isinstance(self, PhoneDevice):
            if self.method == 'call':
                make_call(device=self, token=token)
            else:
                send_sms(device=self, token=token)


class EmailDevice(TFADevice):
    class Meta:
        app_label = 'two_factor'

    drift_range = range(-30, 1)

    def __eq__(self, other):
        if not isinstance(other, EmailDevice):
            return False
        return self.user == other.user and self.key == other.key


class PhoneDevice(TFADevice):
    """
    Model with phone number and token seed linked to a user.
    """
    class Meta:
        app_label = 'two_factor'

    drift_range = range(-5, 1)

    number = PhoneNumberField()
    method = models.CharField(max_length=4, choices=PHONE_METHODS,
                              verbose_name=_('method'))

    def __repr__(self):
        return '<PhoneDevice(number={!r}, method={!r}>'.format(
            self.number,
            self.method,
        )

    def __eq__(self, other):
        if not isinstance(other, PhoneDevice):
            return False
        return self.number == other.number \
            and self.method == other.method \
            and self.key == other.key
