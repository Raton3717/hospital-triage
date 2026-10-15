import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'hospital_secret_key_123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hospital_triage.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    bp_sys = db.Column(db.Integer, nullable=False)
    bp_dia = db.Column(db.Integer, nullable=False)
    spo2 = db.Column(db.Integer, nullable=False)
    pulse = db.Column(db.Integer, nullable=False)
    temperature = db.Column(db.Float, nullable=False)
    triage_priority = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Waiting') # Waiting, Active, Completed
    assigned_chamber = db.Column(db.String(50), nullable=True)
    arrival_time = db.Column(db.DateTime, default=datetime.utcnow)

class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    doctor_name = db.Column(db.String(100), nullable=False)
    diagnosis = db.Column(db.Text, nullable=False)
    medications = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', backref=db.backref('prescriptions', lazy=True))

def calculate_esi_triage(bp_sys, spo2, pulse, temp):
    if spo2 < 90 or bp_sys < 80 or pulse > 130:
        return 1
    elif spo2 < 94 or bp_sys < 90 or pulse > 110 or temp > 103:
        return 2
    elif temp > 101 or bp_sys > 160 or pulse > 100:
        return 3
    elif temp < 96 or bp_sys < 100:
        return 4
    return 5

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    user = User.query.filter_by(username=username, password=password).first()
    if user:
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        return redirect(url_for('dashboard'))
    flash('Invalid Credentials', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    role = session['role']
    # Emergency Triage Priority first, then Arrival Time (Serial) for same priority
    patients = Patient.query.filter(Patient.status != 'Completed').order_by(Patient.triage_priority.asc(), Patient.arrival_time.asc()).all()
    if role == 'staff':
        return render_template('staff_dashboard.html', patients=patients)
    elif role == 'doctor':
        return render_template('doctor_dashboard.html', patients=patients)
    elif role == 'admin':
        all_patients = Patient.query.order_by(Patient.arrival_time.desc()).all()
        return render_template('admin_dashboard.html', patients=all_patients)
    return redirect(url_for('logout'))

@app.route('/add_patient', methods=['POST'])
def add_patient():
    if 'user_id' not in session or session['role'] != 'staff':
        return redirect(url_for('index'))
    name = request.form.get('name')
    age = int(request.form.get('age'))
    gender = request.form.get('gender')
    bp_sys = int(request.form.get('bp_sys'))
    bp_dia = int(request.form.get('bp_dia'))
    spo2 = int(request.form.get('spo2'))
    pulse = int(request.form.get('pulse'))
    temp = float(request.form.get('temperature'))
    
    manual_priority = request.form.get('manual_priority')
    if manual_priority and manual_priority != 'auto':
        triage_priority = int(manual_priority)
    else:
        triage_priority = calculate_esi_triage(bp_sys, spo2, pulse, temp)

    new_patient = Patient(
        name=name, age=age, gender=gender, bp_sys=bp_sys, bp_dia=bp_dia,
        spo2=spo2, pulse=pulse, temperature=temp, triage_priority=triage_priority
    )
    db.session.add(new_patient)
    db.session.commit()
    socketio.emit('queue_updated', {'message': 'Patient added'})
    return redirect(url_for('dashboard'))

@app.route('/edit_patient/<int:id>', methods=['GET', 'POST'])
def edit_patient(id):
    if 'user_id' not in session or session['role'] != 'staff':
        return redirect(url_for('index'))
    patient = Patient.query.get_or_404(id)
    if request.method == 'POST':
        patient.name = request.form.get('name')
        patient.age = int(request.form.get('age'))
        patient.gender = request.form.get('gender')
        patient.bp_sys = int(request.form.get('bp_sys'))
        patient.bp_dia = int(request.form.get('bp_dia'))
        patient.spo2 = int(request.form.get('spo2'))
        patient.pulse = int(request.form.get('pulse'))
        patient.temperature = float(request.form.get('temperature'))
        
        manual_priority = request.form.get('manual_priority')
        if manual_priority and manual_priority != 'auto':
            patient.triage_priority = int(manual_priority)
        else:
            patient.triage_priority = calculate_esi_triage(patient.bp_sys, patient.spo2, patient.pulse, patient.temperature)
            
        db.session.commit()
        socketio.emit('queue_updated', {'message': 'Patient updated'})
        return redirect(url_for('dashboard'))
    return render_template('edit_patient.html', patient=patient)

@app.route('/call_patient/<int:id>', methods=['POST'])
def call_patient(id):
    if 'user_id' not in session or session['role'] != 'doctor':
        return redirect(url_for('index'))
    chamber = request.form.get('chamber')
    patient = Patient.query.get(id)
    if patient:
        patient.status = 'Active'
        patient.assigned_chamber = chamber
        db.session.commit()
        socketio.emit('patient_called', {
            'name': patient.name,
            'id': patient.id,
            'chamber': chamber,
            'priority': patient.triage_priority
        })
    return redirect(url_for('dashboard'))

@app.route('/create_prescription/<int:id>', methods=['POST'])
def create_prescription(id):
    if 'user_id' not in session or session['role'] != 'doctor':
        return redirect(url_for('index'))
    diagnosis = request.form.get('diagnosis')
    medications = request.form.get('medications')
    patient = Patient.query.get(id)
    if patient:
        patient.status = 'Completed'
        prescription = Prescription(
            patient_id=patient.id,
            doctor_name=session['username'],
            diagnosis=diagnosis,
            medications=medications
        )
        db.session.add(prescription)
        db.session.commit()
        socketio.emit('queue_updated', {'message': 'Prescription done'})
        return redirect(url_for('download_prescription_pdf', p_id=prescription.id))
    return redirect(url_for('dashboard'))

@app.route('/download_prescription/<int:p_id>')
def download_prescription_pdf(p_id):
    prescription = Prescription.query.get_or_404(p_id)
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 40px; color: #333;">
        <h2 style="text-align: center; color: #1e3a8a;">RABINDRA MAITREE MEDICAL CENTER</h2>
        <hr/>
        <p><b>Doctor Name:</b> {prescription.doctor_name.upper()}</p>
        <p><b>Date:</b> {prescription.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0; background: #f3f4f6;">
            <tr style="border: 1px solid #ccc;"><td style="padding: 8px;"><b>Patient Name:</b> {prescription.patient.name}</td><td style="padding: 8px;"><b>Age/Gender:</b> {prescription.patient.age} / {prescription.patient.gender}</td></tr>
            <tr style="border: 1px solid #ccc;"><td style="padding: 8px;"><b>Vitals:</b> BP {prescription.patient.bp_sys}/{prescription.patient.bp_dia} | O2 {prescription.patient.spo2}%</td><td style="padding: 8px;"><b>Triage Rank:</b> Tier {prescription.patient.triage_priority}</td></tr>
        </table>
        <h3>Clinical Diagnosis:</h3>
        <p>{prescription.diagnosis}</p>
        <h3>Prescribed Medications:</h3>
        <p>{prescription.medications}</p>
        <br/><br/>
        <p style="text-align: center; font-size: 12px; color: #777;">Generated Dynamically via Rabindra Maitree Hospital System</p>
    </body>
    </html>
    """
    response = make_response(html_content)
    response.headers['Content-Type'] = 'text/html'
    return response

@app.route('/tv-display')
def tv_display():
    patients = Patient.query.filter(Patient.status != 'Completed').order_by(Patient.triage_priority.asc(), Patient.arrival_time.asc()).all()
    return render_template('tv_display.html', patients=patients)

def seed_database():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password='admin123', role='admin')
        db.session.add(admin)
    if not User.query.filter_by(username='staff').first():
        staff = User(username='staff', password='staff123', role='staff')
        db.session.add(staff)
    if not User.query.filter_by(username='doctor1').first():
        doc1 = User(username='doctor1', password='doctor123', role='doctor')
        db.session.add(doc1)
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        seed_database()
    # সার্ভারের দেওয়া পোর্ট রিড করার জন্য নিচের ২ লাইন ব্যবহার করুন
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
