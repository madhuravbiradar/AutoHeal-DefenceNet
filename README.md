=AutoHeal DefenceNet

AI-Powered Self-Healing Network System

🌐 Live Website

Visit our website: https://packet-medic.emergent.host

📌 Project Overview

AutoHeal DefenceNet detects network failures, identifies what failed and where, diagnoses the cause, selects the best recovery plan, and applies recovery actions automatically to restore packet flow.

The simulated network consists of:

- 7 routers arranged in a ring
- 14 access switches
- 112 PCs
- 12 servers

⚙️ Key Features

- Network fault detection and monitoring
- Root-cause diagnosis
- Automatic recovery planning
- Network rerouting and server failover
- Recovery verification
- Incident reporting and audit logs
- Cisco IOS command generation for supported real-device integration

🛠️ Technologies

- Python
- NetworkX
- Matplotlib
- Netmiko (optional for real Cisco device integration)

🚀 How to Run

Install dependencies:

pip install -r requirements.txt

Run the demonstration:

python run_demo.py

Run the quieter report:

python run_demo.py --quiet

Run the tests:

python -m unittest discover -s tests -v

Run the packet recovery demonstration:

python demo_resume.py --fault all

📊 Outputs

The project generates:

- "output/incident_report.json"
- "output/timeline.csv"

⚠️ Limitations

Network latency, bandwidth, and fault behaviour are simulated. Distinguishing jamming from cable cuts relies on heuristic signatures and confidence scores. Hosts behind a failed single-homed switch may require manual intervention.

👩‍💻 Repository

"AutoHeal-DefenceNet on GitHub" (https://github.com/madhuravbiradar/AutoHeal-DefenceNet)
