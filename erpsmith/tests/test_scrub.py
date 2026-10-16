import time

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
	('password = """hunter2"""', "hunter2"),
	("password = \'\'\'hunter2\'\'\'", "hunter2"),
	('password = """line1\nhunter2"""', "hunter2"),
	('password = "a\\"b-hunter2"', "hunter2"),
	("password = 'it\\'s-hunter2'", "hunter2"),
	('token = b"hunter2"', "hunter2"),
	('secret = r"hunter2"', "hunter2"),
	('password = f"hunter2{x}"', "hunter2"),
	('password = rb"hunter2"', "hunter2"),
	('auth = "x9secret"', "x9secret"),
	('auth_token = "x9secret"', "x9secret"),
	('frappe.db.set_value("Email Account", n, "password", "hunter2")', "hunter2"),
	('doc.set("api_key", "abc123XYZ")', "abc123XYZ"),
	('headers["Authorization"] = "token 3f2a9b8c7d6e5f4:1a2b3c4d5e6f7a8"', "3f2a9b8c7d6e5f4"),
	("headers['X-API-KEY'] = 'k9f8e7d6c5b4a3'", "k9f8e7d6c5b4a3"),
	('requests.post(url, auth=("api-user", "S3cure!Pass"))', "S3cure!Pass"),
	('h = f"Authorization: token 3f2a9b8c7d6e5f4a:1a2b3c4d5e6f7a8b"', "3f2a9b8c7d6e5f4a"),
	("k = \"-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQcYBF0abcdefg\n-----END PGP PRIVATE KEY BLOCK-----\"", "lQcYBF0abcdefg"),
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
	'frappe.throw("Basic authentication failed")',
	'msg = "Bearer token is required"',
	'msg = "Basic information"',
	'doc.author = "John Smith"',
	'author_name = "Jane"',
	'total = doc.grand_total\nif total > 1000:\n\tdoc.status = "Review"',
]


def _row(text):
	return next(source for source, _ in SECRETS if text in source)


class TestScrub(FrappeTestCase):
	def test_each_secret_shape_is_redacted(self):
		for source, secret in SECRETS:
			out = scrub(source)
			self.assertNotIn(secret, out, source)
			self.assertTrue("[redacted]" in out or "[data URI]" in out, source)
		self.assertIn("https://[redacted]@api.example.com/x", scrub(_row("requests.get")))
		self.assertIn('"[data URI]"', scrub(_row("data:image")))
		self.assertNotIn("BEGIN", scrub(PEM))

	def test_benign_code_is_unchanged(self):
		for source in BENIGN:
			self.assertEqual(scrub(source), source)

	def test_a_tuple_keeps_its_user_name(self):
		self.assertEqual(scrub('auth=("api-user", "S3cure!Pass")'), 'auth=("api-user", "[redacted]")')

	def test_long_inputs_stay_fast(self):
		for text in ("a" * 50000, '"' + "ab1" * 20000 + '"', "pass" * 500, "password" * 5000, "auth_" * 8000, "token " + "a" * 20000):
			start = time.monotonic()
			scrub(text)
			self.assertLess(time.monotonic() - start, 1, text[:12])

	def test_empty_input(self):
		self.assertEqual((scrub(None), scrub("")), ("", ""))
