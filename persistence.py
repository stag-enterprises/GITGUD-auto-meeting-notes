import streamlit as st
import uuid
from streamlit_local_storage import LocalStorage


class PersistentInputs:
    def __init__(self):
        self.sto = LocalStorage()

        st.markdown("""
            <style>
                [class*="st-key-autosave_api_"] {
                    display: none;
                }
            </style>
        """, unsafe_allow_html=True)

        if "autosave_save_queue" not in st.session_state:
            st.session_state.autosave_save_queue = {}
        if "autosave_del_queue" not in st.session_state:
            st.session_state.autosave_del_queue = []
        if "autosave_keys" not in st.session_state:
            st.session_state.autosave_keys = set()

        self.save_q = st.session_state.autosave_save_queue
        self.del_q = st.session_state.autosave_del_queue
        self.keys = st.session_state.autosave_keys

        self._process_queues()

    def _process_queues(self):
        if self.save_q:
            for key, value in self.save_q.items():
                uid = uuid.uuid4().hex[:8]
                self.sto.setItem(key, value, key=f"autosave_api_{key}_{uid}")

            self.save_q.clear()
            st.toast("changes saved automatically", icon="💾")

        if self.del_q:
            for key in self.del_q:
                uid = uuid.uuid4().hex[:8]
                try:
                    self.sto.deleteItem(key, key=f"autosave_api_{key}_{uid}")
                except KeyError:
                    pass

            self.del_q.clear()
            st.toast("""
                **saved data cleared**\\
                reload the page to clear fields
            """, icon="🗑️")

    def _widget_changed(
        self, store_key, st_key, usr_on_change, usr_args, usr_kwargs
    ):
        val = st.session_state[st_key]
        self.save_q[store_key] = val

        if usr_on_change:
            usr_on_change(*usr_args, **usr_kwargs)

    def create_widget(self, widget, label, store_key=None, value="", **kwargs):
        _store_key = (
            store_key if store_key is not None else label.replace(" ", "_")
        )
        self.keys.add(_store_key)
        st_key = f"autosave_widget_{_store_key}"

        usr_on_change = kwargs.pop("on_change", None)
        usr_args = kwargs.pop("args", tuple())
        usr_kwargs = kwargs.pop("kwargs", dict())

        if st_key not in st.session_state:
            saved = self.sto.getItem(_store_key)
            kwargs["value"] = saved if saved is not None else value

        return widget(
            label=label,
            key=st_key,
            on_change=self._widget_changed,
            args=(_store_key, st_key, usr_on_change, usr_args, usr_kwargs),
            **kwargs,
        )

    def text_input(self, label, store_key=None, value="", **kwargs):
        return self.create_widget(
            st.text_input, label, store_key, value, **kwargs
        )

    def text_area(self, label, store_key=None, value="", **kwargs):
        return self.create_widget(
            st.text_area, label, store_key, value, **kwargs
        )

    def clear_saved_button(self, label="clear saved data", **kwargs):
        if st.button(label, **kwargs):
            for k in list(self.keys):
                self.del_q.append(k)
            st.rerun()
