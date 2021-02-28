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
import encryptPassword from "./crypto.js"

const apiToken = location.hash.slice(1)
const headers = { Authorization: `Bearer ${apiToken}` }
const jsonHeaders = { ...headers, "Content-Type": "application/json" }
const fetchParams = { headers }

export async function whoami() {
	const resp = await fetch("api/whoami", fetchParams)
	return await resp.json()
}

export async function prepareLogin() {
	const resp = await fetch("api/login/prepare", { ...fetchParams, method: "POST" })
	return await resp.json()
}

export async function login(pubkey, keyID, email, password) {
	const resp = await fetch("api/login", {
		method: "POST",
		body: JSON.stringify({
			email,
			encrypted_password: await encryptPassword(pubkey, keyID, password),
		}),
		headers: jsonHeaders,
	})
	return await resp.json()
}

export async function login2FA(email, code) {
	const resp = await fetch("api/login/2fa", {
		method: "POST",
		body: JSON.stringify({ email, code }),
		headers: jsonHeaders,
	})
	return await resp.json()
}

export async function loginApproved() {
	const resp = await fetch("api/login/approved", { method: "POST", headers })
	return await resp.json()
}

export async function wasLoginApproved() {
	const resp = await fetch("api/login/check_approved", fetchParams)
	return (await resp.json()).approved
}
