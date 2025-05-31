from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from datetime import datetime
import json
import csv
import pandas as pd

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('leaderboards', exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    scores = db.relationship('Score', backref='player', lazy=True)

class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    score = db.Column(db.Float, nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    quizzes = db.relationship('Quiz', backref='author', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    questions = db.relationship('Question', backref='quiz', lazy=True, cascade='all, delete-orphan')

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_text = db.Column(db.String(200), nullable=False)
    options = db.Column(db.Text, nullable=False)  # Store options as JSON string
    correct_answer = db.Column(db.String(200), nullable=False)
    image_path = db.Column(db.String(200))  # Add image path field
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)

    def get_options(self):
        return json.loads(self.options)

    def set_options(self, options_list):
        self.options = json.dumps(options_list)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index():
    quizzes = Quiz.query.order_by(Quiz.created_at.desc()).all()
    return render_template('index.html', quizzes=quizzes)

@app.route('/quiz/<int:quiz_id>/play', methods=['GET', 'POST'])
def register_player(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    if request.method == 'POST':
        name = request.form.get('name')
        age = request.form.get('age')
        
        if not name or not age:
            flash('Please provide both name and age')
            return redirect(url_for('register_player', quiz_id=quiz_id))
        
        try:
            age = int(age)
            if age < 4 or age > 18:
                flash('Age must be between 4 and 18')
                return redirect(url_for('register_player', quiz_id=quiz_id))
        except ValueError:
            flash('Please enter a valid age')
            return redirect(url_for('register_player', quiz_id=quiz_id))
        
        player = Player(name=name, age=age)
        db.session.add(player)
        db.session.commit()
        
        session['player_id'] = player.id
        return redirect(url_for('view_quiz', quiz_id=quiz_id))
    
    return render_template('register_player.html', quiz=quiz)

@app.route('/quiz/<int:quiz_id>')
def view_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if 'player_id' not in session:
        return redirect(url_for('register_player', quiz_id=quiz_id))
    return render_template('quiz.html', quiz=quiz)

@app.route('/quiz/<int:quiz_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    # Check if the current user is the author of the quiz
    if quiz.user_id != current_user.id:
        abort(403)  # Forbidden

    if request.method == 'POST':
        quiz.title = request.form.get('title')
        quiz.description = request.form.get('description')
        
        # Handle quiz image upload
        image = request.files.get('image')
        if image and allowed_file(image.filename):
            # Delete old image if exists
            if quiz.image_path:
                old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], quiz.image_path)
                if os.path.exists(old_image_path):
                    os.remove(old_image_path)
            
            # Save new image
            filename = secure_filename(image.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            quiz.image_path = filename

        # Delete existing questions
        Question.query.filter_by(quiz_id=quiz.id).delete()
        
        # Add updated questions
        question_texts = request.form.getlist('question_text[]')
        options_list = request.form.getlist('options[]')
        correct_answers = request.form.getlist('correct_answer[]')
        question_images = request.files.getlist('question_image[]')
        
        for q_text, options, c_answer, q_image in zip(question_texts, options_list, correct_answers, question_images):
            if q_text and options and c_answer:
                options = [opt.strip() for opt in options.split(',') if opt.strip()]
                question = Question(
                    question_text=q_text,
                    correct_answer=c_answer,
                    quiz_id=quiz.id
                )
                question.set_options(options)

                # Handle question image
                if q_image and allowed_file(q_image.filename):
                    filename = secure_filename(q_image.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                    filename = timestamp + filename
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    q_image.save(image_path)
                    question.image_path = filename

                db.session.add(question)
        
        db.session.commit()
        flash('Quiz updated successfully!')
        return redirect(url_for('dashboard'))

    return render_template('edit_quiz.html', quiz=quiz)

@app.route('/quiz/<int:quiz_id>/delete', methods=['POST'])
@login_required
def delete_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    # Check if the current user is the author of the quiz
    if quiz.user_id != current_user.id:
        abort(403)  # Forbidden

    # Delete the quiz image if it exists
    if quiz.image_path:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], quiz.image_path)
        if os.path.exists(image_path):
            os.remove(image_path)

    # Delete the quiz (this will cascade delete questions due to the relationship)
    db.session.delete(quiz)
    db.session.commit()
    
    flash('Quiz deleted successfully!')
    return redirect(url_for('dashboard'))

@app.route('/create_quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        image = request.files.get('image')
        
        if not title:
            flash('Title is required')
            return redirect(url_for('create_quiz'))

        quiz = Quiz(title=title, description=description, user_id=current_user.id)
        
        if image and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            quiz.image_path = filename

        db.session.add(quiz)
        db.session.commit()

        # Add questions with options and images
        question_texts = request.form.getlist('question_text[]')
        options_list = request.form.getlist('options[]')
        correct_answers = request.form.getlist('correct_answer[]')
        question_images = request.files.getlist('question_image[]')
        
        for q_text, options, c_answer, q_image in zip(question_texts, options_list, correct_answers, question_images):
            if q_text and options and c_answer:
                # Split options string into list and clean up
                options = [opt.strip() for opt in options.split(',') if opt.strip()]
                question = Question(
                    question_text=q_text,
                    correct_answer=c_answer,
                    quiz_id=quiz.id
                )
                question.set_options(options)

                # Handle question image
                if q_image and allowed_file(q_image.filename):
                    filename = secure_filename(q_image.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                    filename = timestamp + filename
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    q_image.save(image_path)
                    question.image_path = filename

                db.session.add(question)
        
        db.session.commit()
        flash('Quiz created successfully!')
        return redirect(url_for('dashboard'))

    return render_template('create_quiz.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user_quizzes = Quiz.query.filter_by(user_id=current_user.id).order_by(Quiz.created_at.desc()).all()
    return render_template('dashboard.html', quizzes=user_quizzes)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return redirect(url_for('register'))

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful!')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

def update_leaderboard(quiz_id, player_id, score):
    # Create leaderboard directory if it doesn't exist
    os.makedirs('leaderboards', exist_ok=True)
    
    # Get player and quiz info
    player = Player.query.get(player_id)
    quiz = Quiz.query.get(quiz_id)
    
    # Create a safe filename from the quiz title
    safe_title = "".join(c for c in quiz.title if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_title = safe_title.replace(' ', '_')
    
    # Create or update CSV file
    filename = f'leaderboards/{safe_title}_leaderboard.csv'
    file_exists = os.path.exists(filename)
    
    with open(filename, 'a', newline='') as csvfile:
        fieldnames = ['Player Name', 'Age', 'Score', 'Date']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow({
            'Player Name': player.name,
            'Age': player.age,
            'Score': score,
            'Date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    
    # Also save to database
    score_record = Score(score=score, quiz_id=quiz_id, player_id=player_id)
    db.session.add(score_record)
    db.session.commit()

@app.route('/quiz/<int:quiz_id>/leaderboard')
def view_leaderboard(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    # Create a safe filename from the quiz title
    safe_title = "".join(c for c in quiz.title if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_title = safe_title.replace(' ', '_')
    filename = f'leaderboards/{safe_title}_leaderboard.csv'
    
    if not os.path.exists(filename):
        return render_template('leaderboard.html', quiz=quiz, scores=[])
    
    # Read CSV file
    df = pd.read_csv(filename)
    # Sort by score in descending order
    df = df.sort_values('Score', ascending=False)
    scores = df.to_dict('records')
    
    return render_template('leaderboard.html', quiz=quiz, scores=scores)

@app.route('/quiz/<int:quiz_id>/save_score', methods=['POST'])
def save_score(quiz_id):
    if 'player_id' not in session:
        return jsonify({'error': 'Player not registered'}), 400
    
    data = request.get_json()
    score = data.get('score')
    
    if score is None:
        return jsonify({'error': 'No score provided'}), 400
    
    # Update leaderboard
    update_leaderboard(quiz_id, session['player_id'], score)
    
    return jsonify({'success': True})

# Initialize the database
def init_db():
    with app.app_context():
        db.drop_all()  # Drop all existing tables
        db.create_all()  # Create new tables with updated schema

if __name__ == '__main__':
    init_db()  # Initialize the database before running the app
    app.run(debug=True) 