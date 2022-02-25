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
import struct

import paho.mqtt.client as pmc

# from hyperframe.frame import SettingsFrame, HeadersFrame, DataFrame
# import hpack

http2_header = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


class MQTToTClient(pmc.Client):
    def set_client_id(self, client_id: bytes) -> None:
        self._client_id = client_id

    # This is equivalent to the original _send_connect, except:
    # * the protocol ID is MQTToT.
    # * the client ID is sent without a length.
    # * all extra stuff like wills, usernames, passwords and MQTTv5 is removed.
    def _send_connect(self, keepalive):
        proto_ver = self._protocol
        protocol = b"MQTToT"

        remaining_length = 2 + len(protocol) + 1 + 1 + 2 + len(self._client_id)

        connect_flags = 0x02

        command = pmc.CONNECT
        packet = bytearray()
        packet.append(command)

        self._pack_remaining_length(packet, remaining_length)
        packet.extend(
            struct.pack(
                f"!H{len(protocol)}sBBH",
                len(protocol),
                protocol,
                proto_ver,
                connect_flags,
                keepalive,
            )
        )
        packet.extend(self._client_id)

        # settings = SettingsFrame(settings={
        #     SettingsFrame.HEADER_TABLE_SIZE: 4096,
        #     SettingsFrame.ENABLE_PUSH: 0,
        #     SettingsFrame.MAX_FRAME_SIZE: 16834,
        #     SettingsFrame.MAX_CONCURRENT_STREAMS: 100,
        # })
        # headers = HeadersFrame(stream_id=1, data=hpack.Encoder().encode({
        #     ":method": "POST",
        #     ":scheme": "http",
        #     ":path": "",
        #     ":authority": "edge-mqtt-merge.facebook.com",
        # }))
        # data = DataFrame(stream_id=1, data=packet)
        # packet = http2_header + settings.serialize() + headers.serialize() + data.serialize()

        self._keepalive = keepalive
        self._easy_log(
            pmc.MQTT_LOG_DEBUG,
            "Sending CONNECT",
        )
        return self._packet_queue(command, packet, 0, 0)

    def _packet_handle(self):
        cmd = self._in_packet["command"] & 0xF0
        # Facebook's MQTToT is based on MQTTv3.1, but paho.mqtt only allows DISCONNECT on MQTTv5
        if cmd == pmc.DISCONNECT:
            return self._handle_disconnect()
        return super()._packet_handle()
