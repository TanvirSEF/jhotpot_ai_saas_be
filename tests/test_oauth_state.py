import unittest
import uuid

from fastapi import HTTPException

from app.api.v1.fb import _create_state_token, _decode_state_token
from app.core.security import create_access_token


class OAuthStateTokenTests(unittest.TestCase):
    def test_state_token_has_expected_identity_and_type(self):
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        token, state_id = _create_state_token(org_id, user_id)

        decoded_org, decoded_user, decoded_state = _decode_state_token(token)
        self.assertEqual(decoded_org, org_id)
        self.assertEqual(decoded_user, user_id)
        self.assertEqual(decoded_state, state_id)

    def test_access_token_cannot_be_used_as_oauth_state(self):
        with self.assertRaises(HTTPException) as raised:
            _decode_state_token(create_access_token(str(uuid.uuid4())))

        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
