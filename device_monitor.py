# device_monitor.py
import streamlit as st
import pandas as pd
import threading
import subprocess
import time
import os


def ping(ip):
    try:
        output = subprocess.run(["ping", "-n", "1", ip],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Debug output
        print(f"Ping Output for {ip}:", output.stdout)
        print(f"Ping Error for {ip}:", output.stderr)

        if "TTL=" in output.stdout:
            return "UP", ""
        elif "Request timed out" in output.stdout:
            return "DOWN", "Timeout"
        elif "unreachable" in output.stdout:
            return "DOWN", "Unreachable"
        else:
            return "DOWN", "No response"
    except Exception as e:
        return "DOWN", str(e)


def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def bulk_ping(df, camera_states, down_cameras, progress_bar, progress_text):
    total = len(df)
    completed = 0
    start_time = time.time()

    def worker(ip):
        nonlocal completed
        result, reason = ping(ip)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        camera_states[ip] = (result, now, reason)
        if result == "DOWN":
            down_cameras[ip] = now
        elif ip in down_cameras:
            del down_cameras[ip]
        completed += 1
        elapsed = time.time() - start_time
        avg = elapsed / completed if completed else 0
        remaining = (total - completed) * avg
        progress_bar.progress(completed / total)
        progress_text.text(f"{completed}/{total} (~{format_time(remaining)} remaining)")

    threads = []
    for ip in df["IP"]:
        t = threading.Thread(target=worker, args=(ip,))
        threads.append(t)
        t.start()
        if len(threads) >= 100:
            for t in threads: t.join()
            threads.clear()
    for t in threads: t.join()
    progress_text.text("Bulk check complete.")


def export_results(df, camera_states):
    output_df = pd.DataFrame([
        {
            "Name": row["Name"],
            "IP": row["IP"],
            "Status": camera_states.get(row["IP"], ("-",))[0],
            "Last Checked": camera_states.get(row["IP"], ("-", "-"))[1],
            "Error": camera_states.get(row["IP"], ("-", "-", "-"))[2]
        }
        for _, row in df.iterrows()
    ])
    output_df.to_csv("checked_background_servers.csv", index=False)
    return output_df


def main():
    st.set_page_config(page_title="Device Monitor", layout="wide")
    st.title("Device Monitor")

    uploaded_file = st.file_uploader("Upload Device List CSV", type=["csv"])
    if not uploaded_file:
        st.stop()

    df = pd.read_csv(uploaded_file)
    if df.empty or not {"Name", "IP"}.issubset(df.columns):
        st.error("CSV must contain 'Name' and 'IP' columns.")
        return

    camera_states = st.session_state.setdefault("camera_states", {})
    down_cameras = st.session_state.setdefault("down_cameras", {})

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Device List")
        if st.button("Check All Devices"):
            progress_bar = st.progress(0)
            progress_text = st.empty()
            bulk_ping(df, camera_states, down_cameras, progress_bar, progress_text)

        for _, row in df.iterrows():
            ip = row["IP"]
            name = row["Name"]
            colA, colB, colC, colD, colE = st.columns([2, 2, 2, 3, 1])
            colA.write(name)
            colB.write(ip)
            status, last_checked, error = camera_states.get(ip, ("-", "-", "-"))
            colC.write(status)
            colD.write(last_checked)
            colE.button("Check", key=f"check_{ip}", on_click=lambda ip=ip: camera_states.update({ip: (*ping(ip), time.strftime("%Y-%m-%d %H:%M:%S"))}))

    with col2:
        st.subheader("DOWN Devices")
        for ip, ts in sorted(down_cameras.items(), key=lambda x: x[1]):
            name = df[df["IP"] == ip]["Name"].values[0]
            st.write(f"{name} ({ip}) - DOWN since {ts}")

    if st.button("Export Results to CSV"):
        output_df = export_results(df, camera_states)
        st.success("Exported to 'checked_background_servers.csv'")
        st.download_button("Download CSV", data=output_df.to_csv(index=False).encode('utf-8'), file_name="checked_background_servers.csv", mime="text/csv")


if __name__ == "__main__":
    main()
