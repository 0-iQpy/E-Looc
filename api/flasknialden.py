from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz
import os

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # gamitan natin to ng cryptography yung tinuro ni maem
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///elooc.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Helper function to get current time in Asia/Manila timezone
def get_manila_time():
    manila_tz = pytz.timezone('Asia/Manila')
    return datetime.now(manila_tz)


# Define database models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='admin')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class BulletinPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=get_manila_time)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    creator = db.relationship('User', backref=db.backref('bulletin_posts', lazy=True))


class NewsPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=get_manila_time)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    creator = db.relationship('User', backref=db.backref('news_posts', lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Routes for public pages
@app.route('/')
def index():
    bulletins = BulletinPost.query.filter_by(is_active=True).order_by(BulletinPost.date_posted.desc()).limit(5).all()
    news = NewsPost.query.filter_by(is_active=True).order_by(NewsPost.date_posted.desc()).limit(5).all()
    return render_template('home1.2.html', bulletins=bulletins, news=news)


# Routes for admin authentication
@app.route('/admin')
def admin_redirect():
    return redirect(url_for('admin_login'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            flash('Login successful!', 'success')
            return redirect(next_page if next_page else url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'danger')

    return render_template('admin/login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    flash('You have been logged out', 'success')
    return redirect(url_for('admin_login'))


# Routes for admin dashboard
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    bulletin_count = BulletinPost.query.count()
    news_count = NewsPost.query.count()
    return render_template('admin/dashboard.html', bulletin_count=bulletin_count, news_count=news_count)


# Routes for bulletin management
@app.route('/admin/bulletins')
@login_required
def admin_bulletins():
    bulletins = BulletinPost.query.order_by(BulletinPost.date_posted.desc()).all()
    return render_template('admin/bulletins/index.html', bulletins=bulletins)


@app.route('/admin/bulletins/create', methods=['GET', 'POST'])
@login_required
def admin_create_bulletin():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        is_active = True if request.form.get('is_active') else False

        bulletin = BulletinPost(
            title=title,
            content=content,
            is_active=is_active,
            created_by=current_user.id,
            date_posted=get_manila_time()
        )

        db.session.add(bulletin)
        db.session.commit()

        flash('Bulletin created successfully!', 'success')
        return redirect(url_for('admin_bulletins'))

    return render_template('admin/bulletins/create.html')


@app.route('/admin/bulletins/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_edit_bulletin(id):
    bulletin = BulletinPost.query.get_or_404(id)

    if request.method == 'POST':
        bulletin.title = request.form.get('title')
        bulletin.content = request.form.get('content')
        bulletin.is_active = True if request.form.get('is_active') else False

        db.session.commit()

        flash('Bulletin updated successfully!', 'success')
        return redirect(url_for('admin_bulletins'))

    return render_template('admin/bulletins/edit.html', bulletin=bulletin)


@app.route('/admin/bulletins/delete/<int:id>', methods=['POST'])
@login_required
def admin_delete_bulletin(id):
    bulletin = BulletinPost.query.get_or_404(id)

    db.session.delete(bulletin)
    db.session.commit()

    flash('Bulletin deleted successfully!', 'success')
    return redirect(url_for('admin_bulletins'))


# Routes for news management
@app.route('/admin/news')
@login_required
def admin_news():
    news_items = NewsPost.query.order_by(NewsPost.date_posted.desc()).all()
    return render_template('admin/news/index.html', news_items=news_items)


@app.route('/admin/news/create', methods=['GET', 'POST'])
@login_required
def admin_create_news():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        is_active = True if request.form.get('is_active') else False

        news = NewsPost(
            title=title,
            content=content,
            is_active=is_active,
            created_by=current_user.id,
            date_posted=get_manila_time()
        )

        db.session.add(news)
        db.session.commit()

        flash('News item created successfully!', 'success')
        return redirect(url_for('admin_news'))

    return render_template('admin/news/create.html')


@app.route('/admin/news/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_edit_news(id):
    news = NewsPost.query.get_or_404(id)

    if request.method == 'POST':
        news.title = request.form.get('title')
        news.content = request.form.get('content')
        news.is_active = True if request.form.get('is_active') else False

        db.session.commit()

        flash('News & Events updated successfully!', 'success')
        return redirect(url_for('admin_news'))

    return render_template('admin/news/edit.html', news=news)


@app.route('/admin/news/delete/<int:id>', methods=['POST'])
@login_required
def admin_delete_news(id):
    news = NewsPost.query.get_or_404(id)

    db.session.delete(news)
    db.session.commit()

    flash('News item deleted successfully!', 'success')
    return redirect(url_for('admin_news'))


# Create initial admin user
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    # Check if any user exists
    if User.query.count() > 0:
        flash('Setup has already been completed', 'warning')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        name = request.form.get('name')

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('setup'))

        user = User(username=username, name=name, role='admin')
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash('Initial setup completed. You can now log in.', 'success')
        return redirect(url_for('admin_login'))

    return render_template('admin/setup.html')


# Initialize database if it doesn't exist
def initialize_database():
    if not os.path.exists('api/instance/elooc.db'):
        with app.app_context():
            db.create_all()
            print("Database initialized.")


@app.errorhandler(500)
def internal_error(error):
    return "500 error: {}".format(error), 500


if __name__ == '__main__':
    initialize_database()
    #app.run(debug=True)
    app.run(host="0.0.0.0", debug=True)

