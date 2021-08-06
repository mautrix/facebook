// mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
// Copyright (C) 2021 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

// We have to use this pure-js RSA implementation because SubtleCrypto dropped PKCS#1 v1.5 support.
import RSAKey from "../lib/rsa.min.js"
import ASN1HEX from "../lib/asn1hex-1.1.min.js"

function pemToHex(pem) {
	// Strip pem header
	pem = pem.replace("-----BEGIN PUBLIC KEY-----", "")
	pem = pem.replace("-----END PUBLIC KEY-----", "")

	// Convert base64 to hex
	const raw = atob(pem)
	let result = ""
	for (let i = 0; i < raw.length; i++) {
		const hex = raw.charCodeAt(i).toString(16)
		result += (hex.length === 2 ? hex : "0" + hex)
	}
	return result.toLowerCase()
}

function getKey(pem) {
	const keyHex = pemToHex(pem)
	if (ASN1HEX.isASN1HEX(keyHex) === false) {
		throw new Error("key is not ASN.1 hex string")
	} else if (ASN1HEX.getVbyList(keyHex, 0, [0, 0], "06") !== "2a864886f70d010101") {
		throw new Error("not PKCS8 RSA key")
	} else if (ASN1HEX.getTLVbyListEx(keyHex, 0, [0, 0]) !== "06092a864886f70d010101") {
		throw new Error("not PKCS8 RSA public key")
	}

	const p5hex = ASN1HEX.getTLVbyListEx(keyHex, 0, [1, 0])
	if (ASN1HEX.isASN1HEX(p5hex) === false) {
		throw new Error("keyHex is not ASN.1 hex string")
	}

	const aIdx = ASN1HEX.getChildIdx(p5hex, 0)
	if (aIdx.length !== 2 || p5hex.substr(aIdx[0], 2) !== "02" || p5hex.substr(aIdx[1], 2) !== "02") {
		throw new Error("wrong hex for PKCS#5 public key")
	}

	const hN = ASN1HEX.getV(p5hex, aIdx[0])
	const hE = ASN1HEX.getV(p5hex, aIdx[1])
	const key = new RSAKey()
	key.setPublic(hN, hE)
	return key
}

// encryptPassword encrypts a login password using AES-256-GCM, then encrypts the AES key
// for Facebook's RSA-2048 key using PKCS#1 v1.5 padding.
//
// See https://github.com/mautrix/facebook/blob/v0.3.0/maufbapi/http/login.py#L164-L192
// for the Python implementation of the same encryption protocol.
async function encryptPassword(pubkey, keyID, password) {
	// Key and IV for AES encryption
	const aesKey = await crypto.subtle.generateKey({
		name: "AES-GCM",
		length: 256,
	}, true, ["encrypt", "decrypt"])
	const aesIV = crypto.getRandomValues(new Uint8Array(12))
	// Get the actual bytes of the AES key
	const aesKeyBytes = await crypto.subtle.exportKey("raw", aesKey)

	// Encrypt AES key with Facebook's RSA public key.
	const rsaKey = getKey(pubkey)
	const encryptedAESKeyHex = rsaKey.encrypt(new Uint8Array(aesKeyBytes))
	const encryptedAESKey = new Uint8Array(encryptedAESKeyHex.match(/[0-9A-Fa-f]{2}/g).map(h => parseInt(h, 16)))

	const encoder = new TextEncoder()
	const time = Math.floor(Date.now() / 1000)
	// Encrypt the password. The result includes the ciphertext and AES MAC auth tag.
	const encryptedPasswordBuffer = await crypto.subtle.encrypt({
		name: "AES-GCM",
		iv: aesIV,
		// Add the current time to the additional authenticated data (AAD) section
		additionalData: encoder.encode(time.toString()),
		tagLength: 128,
	}, aesKey, encoder.encode(password))
	// SubtleCrypto returns the auth tag and ciphertext in the wrong order,
	// so we have to flip them around.
	const authTag = new Uint8Array(encryptedPasswordBuffer.slice(-16))
	const encryptedPassword = new Uint8Array(encryptedPasswordBuffer.slice(0, -16))

	const payload = new Uint8Array(2 + aesIV.byteLength + 2 + encryptedAESKey.byteLength + authTag.byteLength + encryptedPassword.byteLength)
	// 1 is presumably the version
	payload[0] = 1
	payload[1] = keyID
	payload.set(aesIV, 2)
	// Length of the encrypted AES key as a little-endian 16-bit int
	payload[aesIV.byteLength + 2] = encryptedAESKey.byteLength & (1 << 8)
	payload[aesIV.byteLength + 3] = encryptedAESKey.byteLength >> 8
	payload.set(encryptedAESKey, 4 + aesIV.byteLength)
	payload.set(authTag, 4 + aesIV.byteLength + encryptedAESKey.byteLength)
	payload.set(encryptedPassword, 4 + aesIV.byteLength + encryptedAESKey.byteLength + authTag.byteLength)
	return `#PWD_MSGR:1:${time}:${btoa(String.fromCharCode(...payload))}`
}

export default encryptPassword
