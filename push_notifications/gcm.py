"""
Google Cloud Messaging
Previously known as C2DM
Documentation is available on the Android Developer website:
https://developer.android.com/google/gcm/index.html
"""

import json
from .models import GCMDevice
import google.auth.transport.requests
from google.oauth2 import service_account


try:
	from urllib.request import Request, urlopen
	from urllib.parse import urlencode
except ImportError:
	# Python 2 support
	from urllib2 import Request, urlopen
	from urllib import urlencode

from django.core.exceptions import ImproperlyConfigured
from . import NotificationError
from .settings import PUSH_NOTIFICATIONS_SETTINGS as SETTINGS


class GCMError(NotificationError):
	pass

def _get_access_token():
	credentials = service_account.Credentials.from_service_account_file(
		SETTINGS["FIREBASE_CREDENTIALS_FILE"],
		scopes=['https://www.googleapis.com/auth/firebase.messaging']
	)
	request = google.auth.transport.requests.Request()
	credentials.refresh(request)
	return credentials.token


def _chunks(l, n):
	"""
	Yield successive chunks from list \a l with a minimum size \a n
	"""
	for i in range(0, len(l), n):
		yield l[i:i + n]


def _gcm_send(data):
	token = _get_access_token()

	headers = {
		'Content-Type': 'application/json; UTF-8',
		'Authorization': 'Bearer ' + token,
	}
	with open(SETTINGS["FIREBASE_CREDENTIALS_FILE"], 'r') as f:
		credentials = json.load(f)

	url = "https://fcm.googleapis.com/v1/projects/" + credentials['project_id'] + "/messages:send"
	request = Request(url, data, headers)
	return urlopen(request).read().decode("utf-8")


def _gcm_send_plain(registration_id, data, **kwargs):
	"""
	Sends a GCM notification to a single registration_id.
	This will send the notification as form data.
	If sending multiple notifications, it is more efficient to use
	gcm_send_bulk_message() with a list of registration_ids
	"""

	msg_objects = {
		'message': {
			'token': registration_id,
			'notification': {
				'title': data.get('title'),
				'body': data.get('message'),
			},
			'data': data,
		},
	}

	for k, v in kwargs.items():
		if v:
			if isinstance(v, bool):
				v = "1"
			msg_objects[k] = v
			
	for k, v in data.items():
		data[k] = str(v)

	data = json.dumps(msg_objects).encode("utf-8")

	result = _gcm_send(data)

	# Information about handling response from Google docs (https://developers.google.com/cloud-messaging/http):
	# If first line starts with id, check second line:
	# If second line starts with registration_id, gets its value and replace the registration tokens in your
	# server database. Otherwise, get the value of Error

	if result.startswith("id"):
		lines = result.split("\n")
		if len(lines) > 1 and lines[1].startswith("registration_id"):
			new_id = lines[1].split("=")[-1]
			_gcm_handle_canonical_id(new_id, registration_id)

	elif result.startswith("Error="):
		if result in ("Error=NotRegistered", "Error=InvalidRegistration"):
			# Deactivate the problematic device
			device = GCMDevice.objects.filter(registration_id=values["registration_id"])
			device.update(active=0)
			return result

		raise GCMError(result)

	return result


def _gcm_send_json(registration_ids, data, **kwargs):
	for registration_id in registration_ids:
		_gcm_send_plain(registration_id, data, **kwargs)


def _gcm_handle_canonical_id(canonical_id, current_id):
	"""
	Handle situation when GCM server response contains canonical ID
	"""
	if GCMDevice.objects.filter(registration_id=canonical_id, active=True).exists():
		GCMDevice.objects.filter(registration_id=current_id).update(active=False)
	else:
		GCMDevice.objects.filter(registration_id=current_id).update(registration_id=canonical_id)


def gcm_send_message(registration_id, data, **kwargs):
	"""
	Sends a GCM notification to a single registration_id.

	If sending multiple notifications, it is more efficient to use
	gcm_send_bulk_message() with a list of registration_ids

	A reference of extra keyword arguments sent to the server is available here:
	https://developers.google.com/cloud-messaging/server-ref#downstream
	"""

	return _gcm_send_plain(registration_id, data, **kwargs)


def gcm_send_bulk_message(registration_ids, data, **kwargs):
	"""
	Sends a GCM notification to one or more registration_ids. The registration_ids
	needs to be a list.
	This will send the notification as json data.

	A reference of extra keyword arguments sent to the server is available here:
	https://developers.google.com/cloud-messaging/server-ref#downstream
	"""

	# GCM only allows up to 1000 reg ids per bulk message
	# https://developer.android.com/google/gcm/gcm.html#request
	max_recipients = SETTINGS.get("GCM_MAX_RECIPIENTS")
	if len(registration_ids) > max_recipients:
		ret = []
		for chunk in _chunks(registration_ids, max_recipients):
			ret.append(_gcm_send_json(chunk, data, **kwargs))
		return ret

	return _gcm_send_json(registration_ids, data, **kwargs)
