from frappe.tests.utils import FrappeTestCase

from erpsmith.scrub import scrub

PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END RSA PRIVATE KEY-----"
SECRETS = [
	('api_key = "abc123XYZ"', "abc123XYZ"),
	('headers = {"Authorization": "Bearer tok.en-1"}', "tok.en-1"),
	('password="hunter2"', "hunter2"),
	("client_secret: 'cs_live_9'", "cs_live_9"),
	('webhook_url = "https://x"', "https://x"),
	('h = "Basic dXNlcjpwYXNz"', "dXNlcjpwYXNz"),
	("k = " + "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2", "a1B2c3D4e5F6g7H8i9J0k1L2"),
	("k = " + "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
	("k = xoxb-1234-5678-abcd", "1234-5678-abcd"),
	("k = AKIA" + "ABCDEFGH12345678", "ABCDEFGH12345678"),
	("k = eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "eyJhbGciOi"),
	(PEM, "MIIBOgIBAAJBAKj34"),
	('requests.get("https://bob:s3cret@api.example.com/x")', "s3cret"),
	('v = "' + "a1b2c3d4e5" * 4 + '"', "a1b2c3d4e5" * 4),
	('img = "data:image/png;base64,iVBORw0KGgo="', "iVBORw0KGgo"),
]
BENIGN = [
	'doc.status = "Approved"',
	'frappe.get_password("Email Account", doc.name, "password")',
	'x = "a1b2c3"',
	'msg = "Please contact the accounts team about the overdue invoice"',
	'frappe.throw("Token expired")',
	'total = doc.grand_total\nif total > 1000:\n\tdoc.status = "Review"',
]


class TestScrub(FrappeTestCase):
	def test_each_secret_shape_is_redacted(self):
		for source, secret in SECRETS:
			out = scrub(source)
			self.assertNotIn(secret, out, source)
			self.assertTrue("[redacted]" in out or "[data URI]" in out, source)
		self.assertIn("https://[redacted]@api.example.com/x", scrub(SECRETS[12][0]))
		self.assertIn('"[data URI]"', scrub(SECRETS[14][0]))
		self.assertNotIn("BEGIN", scrub(PEM))

	def test_benign_code_is_unchanged(self):
		for source in BENIGN:
			self.assertEqual(scrub(source), source)

	def test_empty_input(self):
		self.assertEqual((scrub(None), scrub("")), ("", ""))
