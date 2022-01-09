# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2021 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from typing import Dict, Optional
from uuid import UUID
import random
import string

from attr import dataclass
import attr

from mautrix.types import SerializableAttrs


@dataclass
class AndroidApplication(SerializableAttrs):
    name: str = "Orca-Android"
    version: str = "341.0.0.6.237"
    id: str = "com.facebook.orca"
    locale: str = "en_US"
    build: int = 336996394
    version_id: int = 4768178896567799

    client_id = "256002347743983"
    client_secret = "374e60f8b9bb6b8cbb30f78030438895"

    @classmethod
    def deserialize(cls, data) -> "AndroidApplication":
        data.pop("build")
        data.pop("version_id")
        data.pop("version")
        return super().deserialize(data)

    @property
    def access_token(self) -> str:
        return f"{self.client_id}|{self.client_secret}"


@dataclass
class AndroidDevice(SerializableAttrs):
    manufacturer: str = "Google"
    builder: str = "google"
    name: str = "Pixel 3"
    software: str = "11"
    architecture: str = "arm64-v8a:null"
    dimensions: str = "{density=2.75,width=1080,height=2028}"
    user_agent: str = "Dalvik/2.1.0 (Linux; U; Android 11; Pixel 3 Build/RQ3A.211001.001)"

    connection_type: str = "WIFI"
    connection_quality: str = "EXCELLENT"
    language: str = "en_US"
    country_code: str = "US"

    uuid: Optional[str] = None
    adid: Optional[str] = None

    device_group: Optional[str] = None  # 7761

    @classmethod
    def deserialize(cls, data) -> "AndroidDevice":
        data.pop("software")
        data.pop("user_agent")
        return super().deserialize(data)


@dataclass
class AndroidCarrier(SerializableAttrs):
    name: str = "Verizon"
    hni: int = 311390

    @property
    def mcc(self) -> str:
        return str(self.hni)[:3]

    @property
    def mnc(self) -> str:
        return str(self.hni)[3:]


@dataclass
class AndroidSession(SerializableAttrs):
    access_token: Optional[str] = None
    uid: Optional[int] = None
    password_encryption_pubkey: Optional[str] = None
    password_encryption_key_id: Optional[int] = None
    machine_id: Optional[str] = None
    transient_auth_token: Optional[str] = None
    login_first_factor: Optional[str] = None
    region_hint: str = "ODN"


@dataclass
class AndroidState(SerializableAttrs):
    application: AndroidApplication = attr.ib(factory=lambda: AndroidApplication())
    device: AndroidDevice = attr.ib(factory=lambda: AndroidDevice())
    carrier: AndroidCarrier = attr.ib(factory=lambda: AndroidCarrier())
    session: AndroidSession = attr.ib(factory=lambda: AndroidSession())

    def generate(self, seed: str) -> None:
        rand = random.Random(seed)
        self.device.adid = "".join(rand.choices(string.hexdigits, k=16))
        self.device.uuid = str(UUID(int=rand.getrandbits(128), version=4))
        # TODO randomize carrier and device model

    @property
    def _ua_parts(self) -> Dict[str, str]:
        return {
            "FBAN": self.application.name,
            "FBAV": self.application.version,
            "FBPN": self.application.id,
            "FBLC": self.device.language,
            "FBBV": str(self.application.build),
            "FBCR": self.carrier.name,
            "FBMF": self.device.manufacturer,
            "FBBD": self.device.builder,
            "FBDV": self.device.name,
            "FBSV": self.device.software,
            "FBCA": self.device.architecture,
            "FBDM": self.device.dimensions,
            "FB_FW": "1",
        }

    @property
    def user_agent_meta(self) -> str:
        ua_meta = ";".join(f"{key}/{value}" for key, value in self._ua_parts.items())
        return f"[{ua_meta};]"

    @property
    def user_agent(self) -> str:
        return f"{self.device.user_agent} {self.user_agent_meta}"
