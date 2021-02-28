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
import { h, Component, render } from "../lib/preact-10.5.12.min.js"
import htm from "../lib/htm-3.0.4.min.js"
import * as api from "./api.js"

const html = htm.bind(h)

class App extends Component {
	constructor(props) {
		super(props)
		this.approveCheckInterval = null
		this.state = {
			loading: true,
			submitting: false,
			error: null,
			mxid: null,
			facebook: null,
			status: "pre-login",
			pubkey: null,
			keyID: null,
			email: "",
			password: "",
			twoFactorCode: "",
			twoFactorInfo: {},
		}
	}

	async componentDidMount() {
		const { error, mxid, facebook } = await api.whoami()
		if (error) {
			this.setState({ error, loading: false })
		} else {
			this.setState({ mxid, facebook, loading: false })
		}
	}

	checkLoginApproved = async () => {
		if (!await api.wasLoginApproved()) {
			return
		}
		clearInterval(this.approveCheckInterval)
		this.approveCheckInterval = null
		const resp = await api.loginApproved()
		if (resp.status === "logged-in") {
			this.setState({ status: resp.status })
		}
	}

	submitNoDefault = evt => {
		evt.preventDefault()
		this.submit()
	}

	async submit() {
		if (this.approveCheckInterval) {
			clearInterval(this.approveCheckInterval)
			this.approveCheckInterval = null
		}
		this.setState({ submitting: true })
		let resp
		switch (this.state.status) {
		case "pre-login":
			resp = await api.prepareLogin()
			break
		case "login":
			resp = await api.login(this.state.pubkey, this.state.keyID,
				this.state.email, this.state.password)
			break
		case "two-factor":
			resp = await api.login2FA(this.state.email, this.state.twoFactorCode)
			break
		}
		const stateUpdate = { submitting: false }
		if (typeof resp.error === "string") {
			stateUpdate.error = resp.error
		} else {
			stateUpdate.status = resp.status
		}
		if (resp.password_encryption_key_id) {
			stateUpdate.pubkey = resp.password_encryption_pubkey
			stateUpdate.keyID = resp.password_encryption_key_id
		}
		if (resp.status === "two-factor") {
			this.approveCheckInterval = setInterval(this.checkLoginApproved, 5000)
			stateUpdate.twoFactorInfo = resp.error
		} else if (resp.status === "logged-in") {
			api.whoami().then(({ facebook }) => this.setState({ facebook }))
		}
		this.setState(stateUpdate)
	}

	fieldChange = evt => {
		this.setState({ [evt.target.id]: evt.target.value })
	}

	renderFields() {
		switch (this.state.status) {
		case "pre-login":
			return null
		case "login":
			return html`
				<label for="email">Email</label>
				<input type="email" placeholder="user@example.com" id="email"
					   value=${this.state.email} onChange=${this.fieldChange}/>
				<label for="password">Password</label>
				<input type="password" placeholder="correct horse battery staple" id="password"
					   value=${this.state.password} onChange=${this.fieldChange}/>
			`
		case "two-factor":
			return html`
				<p>${this.state.twoFactorInfo.error_user_msg}</p>
				<label for="email">Email</label>
				<input type="email" placeholder="user@example.com" id="email" disabled
					   value=${this.state.email} onChange=${this.fieldChange}/>
				<label for="twoFactorCode">Two-factor authentication code</label>
				<input type="number" placeholder="123456" id="twoFactorCode"
					   value=${this.state.twoFactorCode} onChange=${this.fieldChange}/>
			`
		}
	}

	submitButtonText() {
		switch (this.state.status) {
		case "pre-login":
			return "Start"
		case "login":
		case "two-factor":
			return "Sign in"
		}
	}

	renderContent() {
		if (this.state.loading) {
			return html`
				<div class="loader">Loading...</div>
			`
		} else if (this.state.status === "logged-in") {
			if (this.state.facebook) {
				return html`
					Successfully logged in as ${this.state.facebook.name}. The bridge will appear
					as ${this.state.facebook.device_displayname} in Facebook security settings.
				`
			}
			return html`
				Successfully logged in
			`
		} else if (this.state.facebook) {
			return html`
				You're already logged in as ${this.state.facebook.name}. The bridge appears
				as ${this.state.facebook.device_displayname} in Facebook security settings.
			`
		}
		return html`
			${this.state.error && html`
				<div class="error button" disabled>${this.state.error}</div>
			`}
			<form onSubmit=${this.submitNoDefault}>
				<fieldset>
					<label for="mxid">Matrix user ID</label>
					<input type="text" placeholder="@user:example.com" id="mxid"
						   value=${this.state.mxid} disabled/>
					${this.renderFields()}
					<button type="submit" disabled=${this.state.submitting}>
						${this.state.submitting
							? "Loading..."
							: this.submitButtonText()}
					</button>
				</fieldset>
			</form>
		`
	}

	render() {
		return html`
			<main>
				<h1>mautrix-facebook login</h1>
				${this.renderContent()}
			</main>
		`
	}
}


render(html`
	<${App}/>
`, document.body)
