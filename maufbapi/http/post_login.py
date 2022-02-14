# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from urllib.parse import urlencode
import json

from ..types.graphql import LoggedInUser
from ..types.graphql.queries import NTContext
from .base import BaseAndroidAPI


class PostLoginAPI(BaseAndroidAPI):
    async def fetch_logged_in_user(self, post_login: bool = False) -> LoggedInUser | None:
        url = self.b_graph_url.with_query(
            {
                "include_headers": "false",
                "decode_body_json": "false",
                "streamable_json_response": "true",
                **self._params,
            }
        )
        req_data = (
            {
                "fb_api_caller_class": "LoginOperations",
                "fb_api_req_friendly_name": "handleLogin",
                "batch": json.dumps(self._post_login_params, separators=(",", ":")),
            }
            if post_login
            else {
                "fb_api_caller_class": "MessagesSyncLoggedInUserFetcher",
                "fb_api_req_friendly_name": "syncRefetchLoggedInUser",
                "batch": json.dumps(self._resync_params, separators=(",", ":")),
            }
        )
        headers = {
            "x-zero-state": "unknown",
            "x-fb-request-analytics-tags": "unknown",
            "x-fb-friendly-name": req_data["fb_api_req_friendly_name"],
            "accept-encoding": "x-fb-dz;d=1, gzip, deflate",
            **self._headers,
        }
        headers.pop("x-fb-rmd", None)
        resp = await self.http.post(url=url, headers=headers, data=req_data)
        await self._decompress_zstd(resp)
        self.log.trace(f"Fetch logged in user response: {await resp.text()}")
        resp_data = await self._handle_response(resp, batch_index=2 if post_login else 0)
        if not post_login:
            # The second batch will sometimes contain errors that the first one doesn't.
            await self._handle_response(resp, batch_index=1)
        try:
            actual_data = resp_data["data"]["viewer"]["actor"]
        except (IndexError, KeyError):
            self.log.warning(
                "Didn't get expected data in fetch logged in user response: %s", resp_data
            )
            return None
        info = LoggedInUser.deserialize(actual_data)
        info.unrecognized_ = {}
        return info

    # fmt: off

    @property
    def _resync_params(self):
        return [
            {
                "method": "POST",
                "body": urlencode({
                    "variables": json.dumps({
                        "profile_pic_small_size": 110,
                        "profile_pic_medium_size": 258,
                        "profile_pic_large_size": 1080,
                        "is_for_messenger": True,
                        "fetch_story_holdout": False,
                    }, separators=(",", ":")),
                    "method": "post",
                    "doc_id": "4043011582467507",
                    "query_name": "GetLoggedInUserQuery",
                    "strip_defaults": "true",
                    "strip_nulls": "true",
                    "locale": self.state.device.language,
                    "client_country_code": self.state.device.country_code,
                    "fb_api_req_friendly_name": "GetLoggedInUserQuery",
                }),
                "name": "user",
                "omit_response_on_success": False,
                "relative_url": "graphql",
            },
            {
                "method": "POST",
                "body": urlencode({
                    "method": "post",
                    "doc_id": "5263028057057401",
                    "query_name": "FetchFacebookEmployeeStatusQuery",
                    "strip_defaults": "true",
                    "strip_nulls": "true",
                    "locale": self.state.device.language,
                    "client_country_code": self.state.device.country_code,
                    "fb_api_req_friendly_name": "FetchFacebookEmployeeStatusQuery",
                }),
                "name": "fetchFacebookEmployeeStatus",
                "omit_response_on_success": False,
                "relative_url": "graphql",
            },
        ]

    @property
    def _post_login_params(self):
        fetch_zero_token_params = {
            "carrier_mcc": self.state.carrier.mcc,
            "carrier_mnc": self.state.carrier.mnc,
            "sim_mcc": self.state.carrier.mcc,
            "sim_mnc": self.state.carrier.mnc,
            "format": "json",
            "interface": self.state.device.connection_type.lower(),
            "dialtone_enabled": "false",
            "needs_backup_rules": "true",
            "request_reason": "login",
            "locale": self.state.device.language,
            "client_country_code": self.state.device.country_code,
            "fb_api_req_friendly_name": "fetchZeroToken",
        }
        return [
            {
                "method": "POST",
                "body": urlencode(fetch_zero_token_params),
                "name": "fetchZeroToken",
                "omit_response_on_success": False,
                "relative_url": "mobile_zero_campaign",
            },
            {
                "method": "POST",
                "body": urlencode({**fetch_zero_token_params, "dialtone_enabled": "true"}),
                "name": "fetchZeroTokenForDialtone",
                "omit_response_on_success": False,
                "relative_url": "mobile_zero_campaign",
            },
            self._resync_params[0],
            {
                "method": "POST",
                "body": urlencode({
                    "variables": json.dumps({
                        "nux_ids": [
                            "9151", "9580", "8587", "5551", "5477", "7441", "6199", "8409", "8330",
                            "6504", "7785", "8435", "6671", "8166", "8071", "10187", "10018",
                            "9682", "10010", "9717", "8432", "8460", "8538", "4442", "9871",
                            "9877", "10186", "9582", "10087", "9890", "7685", "7093", "7841",
                            "6778", "8588", "8266", "9849", "9485", "9455", "9790", "10112",
                            "6849", "3931", "9891", "7721", "7066", "9821", "6762", "5665", "5677",
                            "9606", "9856", "7859", "7826", "7764", "7951", "8478", "7830", "7829",
                            "10003", "6522", "6563", "6005", "7842", "6006", "7979", "7915",
                            "9956", "8449", "5290", "5291", "4828", "7004", "7615", "4745", "4744",
                            "4743", "2415", "4408", "8470", "3545", "5579", "3543", "5411", "8729",
                            "9045", "9808", "9785", "7896", "9608", "8216", "8279", "9423", "7198",
                            "7199", "7190", "7654", "9172", "4541", "9280", "8026", "7987", "8214",
                            "7435", "1820", "9054", "8375", "8543", "4320", "8311", "6389", "4757",
                            "9807", "5131", "4327"
                        ],
                        "device_id": self.state.device.uuid,
                        "nt_context": NTContext().serialize(),
                        "avatar_nux_image_width": 1080,
                        "is_from_internal_tool": False,
                        "family_device_id": self.state.device.fdid,
                        "scale": "3",
                    }, separators=(",", ":")),
                    "method": "post",
                    "doc_id": "4618754314834440",
                    "query_name": "FetchInterstitials",
                    "strip_defaults": "true",
                    "strip_nulls": "true",
                    "locale": self.state.device.language,
                    "client_country_code": self.state.device.country_code,
                    "fb_api_req_friendly_name": "FetchInterstitials"
                }),
                "name": "fetch_interstititals_graphql",
                "omit_response_on_success": False,
                "relative_url": "graphql",
            },
            {
                "method": "POST",
                "body": urlencode({
                    "client_country_code": self.state.device.country_code,
                    "fb_api_req_friendly_name": "fetchGKInfo",
                    "format": "json",
                    "locale": self.state.device.language,
                    "query": "android_aborthooks_enabled,android_actionbar_panel_kb_workaround,android_allow_user_cert_override,android_always_play_live_cached_data,android_analytics_listener_bg_handler,android_block_drm_playback_hdmi,android_block_drm_screen_capture,android_chat_head_hw_accel_disabled,android_chat_heads_app_state,android_cm_holiday_card_render,android_disable_messenger_ineedinits,android_drm_blacklisted_devices,android_enable_maxwidth_prefilter,android_enable_oxygen_crash_reporter,android_enable_terminate_handler,android_enable_vod_prefetch_qs_fix,android_fb4a_enable_zero_ip_test,android_fbns_dumpsys_ids,android_game_tab_badging,android_granular_exposures_navigation,android_headspin_logging,android_invite_link_phone_confirm_enable_gk,android_large_payload_support,android_learn_startvideosession_uri,android_legacy_logging_framework_gk,android_litho_enable_thread_tracing_stacktrace,android_litho_opt_visibility_handlers,android_litho_transitions_extension_290,android_loaded_library_reporting,android_messenger_add_contacts_redesign_gk,android_messenger_android_auto_support,android_messenger_avoid_ipc_logging,android_messenger_background_contact_logs_upload,android_messenger_background_contacts_upload,android_messenger_banner_triggers_omnistore,android_messenger_bug_report_on_instacrash_loop,android_messenger_camera_selfie_effect_gk,android_messenger_composer_lightweight_actions,android_messenger_contact_upload_progress_screens,android_messenger_dark_mode_opt_in,android_messenger_dark_mode_rollout,android_messenger_data_saver_mode,android_messenger_delay_analytics_during_startup,android_messenger_delay_periodic_reporters,android_messenger_employee_default_beta_web_tier,android_messenger_explicitly_adding_extensions,android_messenger_file_based_logger,android_messenger_hole_check_disabled,android_messenger_inapp_browser,android_messenger_instrumented_drawable,android_messenger_log_messaging_debug_events,android_messenger_multiple_requests_money_fab,android_messenger_omnistore_debug_uploader_flytrap,android_messenger_omnistore_rage_shake_sqlite,android_messenger_p2p_payment,android_messenger_periodic_effect_prefetch_enabled,android_messenger_platform_20141218,android_messenger_platform_20150311,android_messenger_platform_20150314,android_messenger_post_capture_effect_gk,android_messenger_privacy_aware_logger,android_messenger_profile_view_redirect_target,android_messenger_rage_shake_enable_loom,android_messenger_rage_shake_in_chat_heads,android_messenger_recipient_holdout_filter,android_messenger_reddit_link,android_messenger_send_or_request_money_fab,android_messenger_show_onboarding_flow,android_messenger_skip_messages_update_on_paused,android_messenger_skip_startup_prefetch,android_messenger_sms_invites_gk,android_messenger_store_in_private_storage,android_mobile_config_sampled_access_disabled,android_mqtt_fast_send,android_mqtt_log_time,android_mqtt_new_wake_lock,android_mqtt_pendingmessage_connect,android_mqtt_reconnect_back_off_fix,android_mqtt_report_connect_sent_state,android_mqttlite_health_stats_sampling,android_mqttlite_log_sampling,android_omnistore_init_using_critical_path_task,android_professional_services_booking,android_qpl_use_freemode_stats,android_qpl_use_image_stats,android_qpl_use_mobile_infra_memory_stats,android_qpl_use_mobileboost_usage,android_qpl_use_msys_info,android_qpl_use_navigation_data,android_qpl_use_nt_stats,android_qpl_use_sapienz_data,android_qpl_use_thermal_stats,android_qpl_use_traffic_transport_monitor_metadata,android_qpl_use_user_perceptible_scopes,android_qpl_use_yoga_provider,android_reliability_lacrima_gk,android_rtc_msqrd_supported,android_run_gc_on_trim,android_show_whitehat_settings,android_soft_error_disabled,android_soft_error_write_to_qpl,android_trusted_tester,android_two_phase_video_spinner,android_video_abr_instrumentation_sampling,android_video_cache_refresh_rate,android_video_delayed_vps_session_release,android_video_detect_plugin_multi_container,android_video_enable_adaptive_caption,android_video_fallback_when_no_reps,android_video_fix_social_player_reload_same_video,android_video_hash_url,android_video_init_constraints_with_highest_format,android_video_live_current_null_as_low_buffer,android_video_live_trace,android_video_live_use_contextual_parameters,android_video_log_stall_detail_event,android_video_playback_getserializable_blacklist,android_video_prefetch_when_no_reps,android_video_profiler_marauder_logging,android_video_refresh_expired_url,android_video_report_prefetch_abr_decision,android_video_resolve_cc_per_process,android_video_resolve_cc_per_video,android_video_rvp_deprecation_notice,android_video_scale_tv_at_ui_thread,android_video_send_debug_headers_to_cdn,android_video_skip_bug_report_extra,android_video_skip_texture_view_get_bitmap,android_video_treat_current_null_as_low_buffer,android_video_use_contextual_network_aware_params,android_video_use_contextual_parameters,android_video_wall_time_protect,android_video_warm_codec,android_whistle_liger_merge,android_whistle_proxy_support,android_whitehat_setting_intercept_traffic,android_zero_optin_graphql_fetch,android_zero_rating_header_request,app_module_download,boost_counters_activity_thread,boost_counters_binder,boost_counters_blockidlejobs,boost_counters_cpuboost,boost_counters_delayedanalytics,boost_counters_graphql,boost_counters_io_thread_periodic,boost_counters_litho_layout_thread,boost_counters_renderthreadboost,boost_counters_smart_fsync,boost_counters_smartgc,boost_counters_softkeyboard,boost_counters_threadaffinity,boost_counters_ui_thread,boost_counters_ui_thread_periodic,campaign_api_use_backup_rules,ccu_content_01,ctm_in_thread_warning_show_report,debug_logs_defaulted_on_android,dialtone_android_eligibility,disable_zero_h_conditional_worker,disable_zero_optin_conditional_worker,disable_zero_token_conditional_worker,enable_crashreport_gk_dump,enable_crashreport_mobileconfig_dump,enable_multi_sso_logging_killswitch,enable_rewrite_for_heroplayer_killswitch,fb4a_allow_carrier_signal_on_wifi,fb4a_enable_io_logging_across_add_dexes,fb4a_internal_relogin_gk,fb4a_report_low_memory_event,fb4a_sample_prefetch_abr_at_qpl_logger,fb_app_zero_rating,fbandroid_disable_memory_trimmable,fbandroid_network_data_logger_add_enabled_features,gk_player_service_blacklist,is_employee,is_employee_public,killswitch_zero_h_ping_conditional_worker,litho_error_boundaries,ls_enable_community_messaging,m2a_overall_calling_killswitch,m4a_nux_pna_target_country,marauder_mobile_power_metrics_logging,mature_content_rating,message_attachment_size_control,messages_android_quickcam_profile,messenger_chat_head_notif_info_action_disabled,messenger_chat_heads_android,messenger_client_analytics_android,messenger_force_full_reliability_logging_android,messenger_inbox_unit_visibility_history_logging,messenger_internal_prefs_android,messenger_list_creator_debugger,messenger_marketplace_rating_use_webview,messenger_new_friend_bump_updated_launch,messenger_new_message_anchor,messenger_notification_working_group,messenger_opened_thread_from_notif_kill_switch,messenger_preload_startup_classes_asap,messenger_sms_takeover_rollout,messenger_sticker_search_android,messenger_wear_enable,mn_cowatching_android_device_eligibility,mobile_native_soft_errors,mobile_zero_show_use_data_or_stay_free_screen,mqtt_client_network_tracing,mqtt_whistle_android_unified_client_logging,msgr_android_employee_viewability_logging_debug,msgr_nux_pna_allowlist,multidexclassloader_artnative_modelspecific,orca_invite_banner_killswitch_gk,p2p_allow_product_override,p2p_android_request_eligible,p2p_android_send,p2p_android_settings,p2p_enabled_in_groups_android,p2p_has_user_added_credential_before,p2p_v2_group_requests_android,p2p_v2_local_bubble,pages_call_deflection_upsell_card_killswitch,payments_settings_p2p_entry_point,prefetch_inbox2_only_if_necessary,prefetch_thread_list_only_if_necessary,preinflate_message_item_view,rtc_android_openh264_kill_switch,rtc_coex_script_logging,rtc_fb4a_openh264_voltron_kill_switch,rtc_h264_android_device_blacklist,rtc_h265_android_device_blacklist,rtc_use_sdp_renegotiation,services_admin_export_to_calendar,skip_mount_setvisibilityhint,sms_takeover_legacy_fallback_devices,top_level_voip_call_button,use_bootstrap_zero_native,video_inline_android_shutoff,video_prefetch_fb4a,voip_audio_mode_in_call_android,voip_audio_mode_normal_android,webrtc_disable_diagnostics_folder,whistle_android,zero_backup_rewrite_rules,zero_fb4a_timespent_fix,zero_header_send_state,zero_rating_enabled_on_wifi,zero_token_header_response,zero_token_new_unknown_state_flow,zero_torque_traffic_enforcement",
                    "query_hash": "B2B524B9816B08FC2F38E8BAA941A8F9E02CF453",
                }),
                "name": "gk",
                "omit_response_on_success": False,
                "relative_url": "mobile_gatekeepers",
            },
        ]
