# Deploy on AWS EC2

## No domain. No ports. No web server.

This agent is a background process — it just loops every 10 minutes,
scans markets, and trades. You only interact with it via SSH.

---

## Step 1: SSH into your EC2

```bash
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

If you're using Amazon Linux instead of Ubuntu, replace `ubuntu` with `ec2-user`.

---

## Step 2: Install Python

**Ubuntu (22.04 / 24.04):**
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

**Amazon Linux 2023:**
```bash
sudo dnf install -y python3.11 python3.11-pip
```

---

## Step 3: Set up the project

```bash
# Create project directory
mkdir -p ~/polymarket-agent
cd ~/polymarket-agent
```

Upload your files. From **your local machine**:
```bash
scp -i your-key.pem -r polymarket-agent/* ubuntu@YOUR_EC2_IP:~/polymarket-agent/
```

Or if you pushed to a private GitHub repo:
```bash
git clone https://github.com/YOURNAME/polymarket-agent.git
cd polymarket-agent
```

---

## Step 4: Install dependencies

On Ubuntu 22.04+, system Python is "externally managed" — you must use a virtual environment (do not use `pip3 install` system-wide).

```bash
cd /var/www/polymarket-agent   # or ~/polymarket-agent
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

**If you get `ModuleNotFoundError: No module named 'structlog'`** — install and run using the venv's Python by full path (no reliance on `activate` or `pip` script):

```bash
cd /var/www/polymarket-agent
/var/www/polymarket-agent/venv/bin/python -m pip install -r requirements.txt
/var/www/polymarket-agent/venv/bin/python main.py
```

Or use the run script (installs deps then runs):

```bash
cd /var/www/polymarket-agent
chmod +x run.sh
./run.sh
```

---

## Step 5: Configure

```bash
cp .env.example .env
nano .env
```

Fill in your keys:
```env
PRIVATE_KEY=0xYOUR_POLYGON_PRIVATE_KEY
FUNDER_ADDRESS=0xYOUR_POLYMARKET_DEPOSIT_ADDRESS
ANTHROPIC_API_KEY=sk-ant-api03-XXXXX
SIGNATURE_TYPE=1
STARTING_BANKROLL=50.0
```

Save: `Ctrl+X`, then `Y`, then `Enter`.

---

## Step 6: One-time setup (allowances)

```bash
source venv/bin/activate
python balance.py              # check your wallet
python setup_allowances.py     # approve Polymarket contracts
```

---

## Step 7: Test run

```bash
python main.py
```

Watch the output. You should see:
```
============================================================
🤖 POLYMARKET AUTONOMOUS TRADING AGENT
   Starting bankroll: $50.00
   ...
🔍 Running pre-flight checks...
   💰 USDC balance: $50.00
   ✅ Token allowances OK
   ✅ CLOB API connected

🚀 Agent starting with $50.00 bankroll
```

Press `Ctrl+C` to stop the test.

---

## Step 8: Run permanently (3 options)

### Option A: systemd (recommended — survives reboots)

```bash
sudo tee /etc/systemd/system/polyagent.service << 'EOF'
[Unit]
Description=Polymarket Trading Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket-agent
ExecStart=/home/ubuntu/polymarket-agent/venv/bin/python main.py
Restart=on-failure
RestartSec=60
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable polyagent
sudo systemctl start polyagent
```

**Monitor:**
```bash
# Live logs
sudo journalctl -u polyagent -f

# Status
sudo systemctl status polyagent

# Restart after changes
sudo systemctl restart polyagent

# Stop
sudo systemctl stop polyagent
```

---

### Option B: tmux (quick and easy)

```bash
# Install tmux
sudo apt install -y tmux    # Ubuntu
sudo dnf install -y tmux    # Amazon Linux

# Start a persistent session
tmux new -s agent

# Inside tmux:
cd ~/polymarket-agent
source venv/bin/activate
python main.py

# Detach (agent keeps running): press Ctrl+B, then D

# Reattach later:
tmux attach -t agent
```

The downside: tmux sessions die on reboot. Use systemd for production.

---

### Option C: nohup (simplest, no extras needed)

```bash
cd ~/polymarket-agent
source venv/bin/activate
nohup python main.py > agent.log 2>&1 &

# Check logs
tail -f agent.log

# Find and stop it
ps aux | grep main.py
kill <PID>
```

---

## EC2 Security Group

You do NOT need to open any inbound ports for this agent.
It only makes outbound HTTPS requests to:

- `clob.polymarket.com` (trading)
- `gamma-api.polymarket.com` (market data)
- `api.anthropic.com` (Claude)
- `api.weather.gov` (NOAA)
- `polygon-rpc.com` (balance checks)

Your default EC2 security group already allows all outbound traffic.
Just keep SSH (port 22) open for yourself.

---

## EC2 Instance Size

The agent uses almost no resources:
- ~50MB RAM
- Near-zero CPU (sleeps 10 min between cycles)
- Minimal network

A **t3.micro** or even **t3.nano** ($3.50/month) is more than enough.
If you're in the AWS free tier, a **t2.micro** works perfectly for 12 months free.

---

## Keep EC2 Running

Make sure your EC2 instance doesn't auto-stop:

1. Go to **EC2 Console** → **Instances** → select yours
2. Check that **Instance state** is `running`
3. **Stop protection**: Actions → Instance Settings → Change Stop Protection → Enable
4. **Termination protection**: Actions → Instance Settings → Change Termination Protection → Enable

---

## Quick Reference

```bash
# SSH in
ssh -i your-key.pem ubuntu@YOUR_EC2_IP

# Check if agent is running
sudo systemctl status polyagent

# Watch live logs
sudo journalctl -u polyagent -f

# Check agent state (P&L, trades, bankroll)
cat ~/polymarket-agent/agent_state.json | python3 -m json.tool

# Restart after editing code
sudo systemctl restart polyagent

# Check wallet balance
cd ~/polymarket-agent && source venv/bin/activate && python balance.py
```
