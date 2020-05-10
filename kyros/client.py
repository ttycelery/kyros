import asyncio
import base64
import json

import donna25519

from . import session, constants, crypto, utilities, websocket


class ClientProfile:
    version = constants.CLIENT_VERSION
    long_description = constants.CLIENT_LONG_DESC
    short_description = constants.CLIENT_SHORT_DESC


class Client:
    @classmethod
    async def create(cls):
        instance = cls()
        await instance.setup_ws()
        return instance

    def __init__(self):
        self.profile = ClientProfile()
        self.session = session.Session()
        self.session.client_id = utilities.generate_client_id()
        self.session.private_key = donna25519.PrivateKey()
        self.session.public_key = self.session.private_key.get_public()

    async def setup_ws(self):
        self.ws = websocket.WebsocketClient()
        await self.ws.connect()
        await self.ws.start_receiving()

    def load_profile(self, profile):
        self.profile = profile

    def encode_ws_message(self, obj):
        message_tag = utilities.generate_message_tag()
        message = f"{message_tag},{json.dumps(obj)}"
        return {"tag": message_tag, "data": message}

    def decode_ws_message(self, message):
        tag, json_obj = message.split(",", 1)
        return {"tag": tag, "data": json.loads(json_obj)}

    async def send_init(self):
        init_message = websocket.WebsocketMessage(None, [
            "admin", "init", self.profile.version,
            [self.profile.long_description, self.profile.short_description],
            self.session.client_id, True
        ])
        await self.ws.send_message(init_message)

        resp = await self.ws.messages.get(init_message.tag)
        if resp["status"] != 200:
            raise Exception(f"login failed, message: {resp}")

        self.session.server_id = resp["ref"]

    async def qr_login(self):
        await self.send_init()

        async def wait_qr_scan():
            message = await self.ws.messages.get("s1")
            connection_data = message[1]

            self.session.secret = base64.b64decode(
                connection_data["secret"].encode())
            self.session.server_token = connection_data["serverToken"]
            self.session.client_token = connection_data["clientToken"]
            self.session.browser_token = connection_data["browserToken"]

            self.session.shared_secret = self.session.private_key.do_exchange(
                donna25519.PublicKey(self.session.secret[:32]))
            self.session.shared_secret_expanded = crypto.hkdf_expand(
                self.session.shared_secret, 80)

            if not crypto.validate_secrets(
                    self.session.secret, self.session.shared_secret_expanded):
                raise Exception("HMAC validation failed")

            self.session.keys_encrypted = self.session.shared_secret_expanded[
                64:] + self.session.secret[64:]
            self.session.keys_decrypted = crypto.aes_decrypt(
                self.session.shared_secret_expanded[:32],
                self.session.keys_encrypted)

            self.session.enc_key = self.session.keys_decrypted[:32]
            self.session.mac_key = self.session.keys_decrypted[32:64]
            print(self.session.enc_key, self.session.mac_key)

        qr_fragments = [
            self.session.server_id,
            base64.b64encode(self.session.public_key.public).decode(),
            self.session.client_id
        ]
        qr = ",".join(qr_fragments)

        return qr, asyncio.wait_for(wait_qr_scan(), 20)
