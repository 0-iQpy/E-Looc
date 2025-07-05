import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import pytz
from dateutil import parser
from dotenv import load_dotenv
from supabase import create_client, Client
import time
import traceback

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception(
        "SUPABASE_URL and SUPABASE_KEY must be set in your environment variables"
    )
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"

# In app.py, modify get_manila_time() function
def get_manila_time():
    manila_tz = pytz.timezone("Asia/Manila")
    return datetime.now(manila_tz)

# Helper function to upload image to Supabase Storage
def upload_to_supabase_storage(file, bucket_name):
    if not file or not file.filename:
        app.logger.info("upload_to_supabase_storage: No file or filename provided.")
        return None

    try:
        # Corrected f-string:
        filename = f"{int(time.time())}_{secure_filename(file.filename)}"

        app.logger.info(f"Attempting to upload {filename} to bucket {bucket_name}")

        # Perform the upload
        supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file.read(), # file.read() is important to get bytes
            file_options={"content-type": file.content_type}
        )

        # If no exception was raised, the upload is successful.
        # Get the public URL using the Supabase client's method.
        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
        app.logger.info(f"Successfully uploaded {filename} to {bucket_name}. Public URL: {public_url}")
        return public_url

    except Exception as e:
        app.logger.error(f"Error uploading {file.filename if file else 'unknown file'} to {bucket_name}: {type(e).__name__} - {str(e)}")
        return None

# Helper function to delete image from Supabase Storage
def delete_from_supabase_storage(image_url, bucket_name):
    app.logger.info(f"delete_from_supabase_storage called with image_url: '{image_url}', bucket_name: '{bucket_name}'")
    if not image_url:
        app.logger.info("delete_from_supabase_storage: No image_url provided. Exiting.")
        return False # No URL, so nothing to delete, but not an error in deletion itself. Consider if True is better. For now, False.

    filename = ""
    try:
        app.logger.debug(f"Attempting to extract filename. Splitting URL '{image_url}' by '/{bucket_name}/'")
        parts = image_url.split(f"/{bucket_name}/")

        if len(parts) < 2 or not parts[1]:
            app.logger.warning(f"Could not extract valid filename. URL: '{image_url}', Bucket: '{bucket_name}'. Parts: {parts}. Exiting.")
            return False

        filename = parts[1]
        if '?' in filename: # Remove query parameters if they exist
            filename = filename.split('?')[0]
        app.logger.info(f"Extracted filename for deletion: '{filename}'")

        # Attempt to remove the file
        app.logger.info(f"Attempting Supabase storage.from_('{bucket_name}').remove(['{filename}'])")
        response_list = supabase.storage.from_(bucket_name).remove([filename])
        app.logger.info(f"Supabase raw response for deleting '{filename}': {response_list}")

        if response_list is None:
            app.logger.error(f"Supabase returned None response for deletion of '{filename}'. This is unexpected. Assuming failure.")
            return False

        # Default to True. If response_list is empty (e.g., file not found, or simple success), it's a success.
        # If there are items in response_list, we check them for errors.
        deletion_successful = True
        for item_idx, item in enumerate(response_list):
            app.logger.debug(f"Processing response item {item_idx} for '{filename}': {item}")
            if not isinstance(item, dict):
                app.logger.error(f"Response item {item_idx} for '{filename}' is not a dict: {item}. Marking as failure.")
                deletion_successful = False
                break # An invalid response item means we can't be sure, so flag as error

            # Check for an error field within the item. Supabase often uses 'error' key.
            # It could be None or an object.
            item_error = item.get("error")
            if item_error is not None: # If 'error' key exists and is not None, it's an error
                app.logger.error(f"Deletion failure reported by Supabase for '{filename}' (item {item_idx}). Error: '{item_error}'. Message: '{item.get('message', 'N/A')}'. Full item: {item}")
                deletion_successful = False
                break # Single file deletion, one error means failure
            else:
                # No 'error' key, or 'error' key is None. This is good.
                # Check for 'message' field for more info, some supabase versions might put "Successfully deleted" or similar here
                # item_message = item.get("message")
                # item_status_code = item.get("status_code", item.get("status"))
                # For now, the absence of an 'error' object is sufficient for success per item.
                app.logger.info(f"No error reported in response item {item_idx} for '{filename}'. Assuming success for this item. Item: {item}")

        app.logger.info(f"Exiting delete_from_supabase_storage for '{filename}'. Overall success: {deletion_successful}")
        return deletion_successful

    except Exception as e:
        # Log detailed error including filename if available
        log_filename = filename if filename else f"unknown (URL: {image_url})"
        app.logger.error(f"Exception in delete_from_supabase_storage for '{log_filename}' from bucket '{bucket_name}'. Type: {type(e).__name__}. Error: {str(e)}. Traceback: {traceback.format_exc()}")
        return False

class User(UserMixin):
    def __init__(self, id, username, password_hash, name, role):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.name = name
        self.role = role

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        resp = supabase.table("users").select("*").eq("id", int(user_id)).single().execute()
        if resp.data:
            user = resp.data
            return User(
                id=user["id"],
                username=user["username"],
                password_hash=user["password_hash"],
                name=user["name"],
                role=user["role"],
            )
    except Exception:
        pass
    return None


@app.route("/")
def index():
    bulletins_resp = (
        supabase.table("bulletin_posts")
        .select("*, image_url")
        .eq("is_active", True)
        .order("date_posted", desc=True)
        .limit(8)
        .execute()
    )
    news_resp = (
        supabase.table("news_posts")
        .select("*, image_url")
        .eq("is_active", True)
        .order("date_posted", desc=True)
        .limit(8)
        .execute()
    )
    bulletins = bulletins_resp.data or []
    news = news_resp.data or []
    manila_time = get_manila_time().strftime("%B %d, %Y %I:%M %p")
    return render_template("home.html", bulletins=bulletins, news=news)


@app.route("/admin")
def admin_redirect():
    return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        try:
            user_resp = supabase.table("users").select("*").eq("username", username).single().execute()
            user = user_resp.data
        except Exception:
            user = None

        if user and check_password_hash(user["password_hash"], password):
            user_obj = User(
                user["id"],
                user["username"],
                user["password_hash"],
                user["name"],
                user["role"],
            )
            login_user(user_obj)
            flash("Login successful!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("admin_dashboard"))
        else:
            flash("Invalid username or password", "danger")

    return render_template("admin/login.html")


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    flash("You have been logged out", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    bulletin_count_resp = supabase.table("bulletin_posts").select("id", count="exact").execute()
    news_count_resp = supabase.table("news_posts").select("id", count="exact").execute()
    patch_notes_resp = supabase.table("patch_notes").select("*").order("date", desc=True).execute()
    system_maintenance_resp = supabase.table("system_maintenance").select("*").order("start_time", desc=True).execute()

    bulletin_count = bulletin_count_resp.count or 0
    news_count = news_count_resp.count or 0
    patch_notes = patch_notes_resp.data or []
    system_maintenance = system_maintenance_resp.data or []

    # Fetch unread notifications
    # Fetch unread notifications with optional filtering
    form_type_filter = request.args.get("form_type_filter")
    notification_query = supabase.table("notifications").select("*", count="exact").eq("is_read", False)

    if form_type_filter:
        notification_query = notification_query.eq("form_type", form_type_filter)

    unread_notifications_resp = notification_query.order("created_at", desc=True).execute()
    unread_notifications = unread_notifications_resp.data or []
    unread_notifications_count = unread_notifications_resp.count or 0

    # Fetch distinct form types for the filter dropdown from unread notifications
    # This is simpler than a separate DB call if the list of types isn't excessively large or needed when no notifications exist.
    # For a more robust solution with many form types, a separate query or RPC might be better.
    all_form_types_for_filter = []
    if unread_notifications_count > 0: # Only try to get form types if there are notifications
        # To get all possible form_types even if some are filtered out by current view:
        all_types_query_resp = supabase.table("notifications").select("form_type").eq("is_read", False).execute()
        if all_types_query_resp.data:
             all_form_types_for_filter = sorted(list(set(n['form_type'] for n in all_types_query_resp.data if n['form_type'])))
        else: # Fallback if above fails or returns nothing, use types from current view
            all_form_types_for_filter = sorted(list(set(n['form_type'] for n in unread_notifications if n['form_type'])))


    return render_template(
        "admin/dashboard.html",
        bulletin_count=bulletin_count,
        news_count=news_count,
        patch_notes=patch_notes,
        system_maintenance=system_maintenance,
        unread_notifications=unread_notifications,
        unread_notifications_count=unread_notifications_count,
        all_form_types_for_filter=all_form_types_for_filter,
        current_form_type_filter=form_type_filter,
    )

@app.route("/admin/notifications/mark-as-read/<int:notification_id>", methods=["POST"])
@login_required
def mark_notification_as_read(notification_id):
    try:
        # Check if notification exists and belongs to the user or is globally updatable by admin
        # For simplicity, we'll assume any admin can mark any notification as read.
        # In a multi-tenant system, you'd add more checks.
        notification_resp = supabase.table("notifications").select("id").eq("id", notification_id).single().execute()
        if not notification_resp.data:
            flash("Notification not found.", "danger")
            return redirect(url_for("admin_dashboard")) # Or return jsonify error if called via JS

        update_resp = supabase.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()

        if hasattr(update_resp, 'data') and update_resp.data:
            flash("Notification marked as read.", "success")
        elif hasattr(update_resp, 'error') and update_resp.error:
            app.logger.error(f"Error marking notification {notification_id} as read: {update_resp.error}")
            flash(f"Error marking notification as read: {update_resp.error.message}", "danger")
        else:
            # This case might occur if the record was already updated or RLS prevented the update without error
            app.logger.warning(f"Notification {notification_id} mark as read returned no data and no error. Might be already read or RLS issue.")
            flash("Notification status unchanged or already read.", "info")


    except Exception as e:
        app.logger.error(f"Exception marking notification {notification_id} as read: {type(e).__name__} - {str(e)}")
        flash("An unexpected error occurred.", "danger")

    # If called via JS, might return jsonify. For simple form/link, redirect.
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/notifications/mark-all-as-read", methods=["POST"])
@login_required
def admin_mark_all_notifications_as_read():
    try:
        update_resp = supabase.table("notifications").update({"is_read": True}).eq("is_read", False).execute()

        # Check if 'data' attribute exists and if it's not empty,
        # or if count attribute suggests changes were made.
        # Supabase update often returns a list of updated records in 'data'.
        # If 'data' is empty but no error, it might mean no records matched the condition.
        if hasattr(update_resp, 'data') and update_resp.data:
            count = len(update_resp.data)
            flash(f"{count} notification(s) marked as read.", "success")
        elif hasattr(update_resp, 'error') and update_resp.error:
            app.logger.error(f"Error marking all notifications as read: {update_resp.error}")
            flash(f"Error marking all notifications as read: {update_resp.error.message}", "danger")
        else:
            # This case could mean no unread notifications were found, which isn't an error.
            flash("No unread notifications to mark as read.", "info")

    except Exception as e:
        app.logger.error(f"Exception marking all notifications as read: {type(e).__name__} - {str(e)}")
        flash("An unexpected error occurred while marking all notifications as read.", "danger")
    return redirect(url_for("admin_dashboard"))


@app.context_processor
def inject_unread_notifications_count():
    if current_user.is_authenticated:
        try:
            resp = (
                supabase.table("notifications")
                .select("id", count="exact")
                .eq("is_read", False)
                .execute()
            )
            count = resp.count or 0
            return dict(unread_notifications_global_count=count)
        except Exception as e:
            app.logger.error(f"Error fetching unread notifications count for context processor: {e}")
            return dict(unread_notifications_global_count=0)
    return dict(unread_notifications_global_count=0)


# Bulletin Management

@app.route("/admin/bulletins")
@login_required
def admin_bulletins():
    bulletins_resp = supabase.table("bulletin_posts").select("*, image_url").order("date_posted", desc=True).execute()
    bulletins = bulletins_resp.data or []
    return render_template("admin/bulletins/index.html", bulletins=bulletins)


@app.route("/admin/bulletins/create", methods=["GET", "POST"])
@login_required
def admin_create_bulletin():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        image_url = None

        if image_file and image_file.filename: # Check if a file was provided
            image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if image_url is None: # Check if upload failed
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/bulletins/create.html")
        else:
            image_url = None


        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
        }
        if image_url: # Only include image_url if it's not None
            data["image_url"] = image_url

        supabase.table("bulletin_posts").insert(data).execute()

        flash("Bulletin created successfully!", "success")
        return redirect(url_for("admin_bulletins"))

    return render_template("admin/bulletins/create.html")


@app.route("/admin/bulletins/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_bulletin(id):
    try:
        resp = supabase.table("bulletin_posts").select("*, image_url").eq("id", id).single().execute()
        bulletin_from_db = resp.data
    except Exception as e:
        app.logger.error(f"Error fetching bulletin id {id} for edit: {type(e).__name__} - {str(e)}")
        flash(f"An error occurred while fetching bulletin details.", "danger")
        return redirect(url_for("admin_bulletins"))

    if not bulletin_from_db:
        flash("Bulletin not found", "danger")
        return redirect(url_for("admin_bulletins"))

    form_data_for_template = bulletin_from_db.copy() # Use a copy for form data

    if request.method == "POST":
        form_data_for_template["title"] = request.form.get("title")
        form_data_for_template["content"] = request.form.get("content")
        form_data_for_template["is_active"] = bool(request.form.get("is_active"))

        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"

        current_db_image_url = bulletin_from_db.get("image_url")
        new_image_url_to_set = current_db_image_url

        # Image handling logic
        if remove_image:
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "bulletin-images"):
                    flash("Failed to delete the current image. Item not updated.", "danger")
                    # form_data_for_template['image_url'] is already current_db_image_url
                    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)
                new_image_url_to_set = None
            else: # No image to remove
                new_image_url_to_set = None
        elif image_file and image_file.filename: # Check filename to ensure a file was actually uploaded
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "bulletin-images"):
                    flash("Failed to delete old image before uploading new. Item not updated.", "danger")
                    # form_data_for_template['image_url'] is already current_db_image_url
                    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

            uploaded_image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if not uploaded_image_url:
                flash("New image upload failed. Item not updated.", "danger")
                # form_data_for_template['image_url'] is already current_db_image_url
                return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)
            new_image_url_to_set = uploaded_image_url

        form_data_for_template["image_url"] = new_image_url_to_set

        # Prepare data for DB update
        update_data_for_db = {
            "title": form_data_for_template["title"],
            "content": form_data_for_template["content"],
            "is_active": form_data_for_template["is_active"],
        }

        if new_image_url_to_set != current_db_image_url:
            update_data_for_db["image_url"] = new_image_url_to_set

        # Database operation
        if not update_data_for_db and new_image_url_to_set == current_db_image_url : # Check if there's anything to update
             flash("No changes detected.", "info")
             return redirect(url_for("admin_bulletins"))

        try:
            response = supabase.table("bulletin_posts").update(update_data_for_db).eq("id", id).execute()
            # Supabase Python client typically raises an exception for HTTP errors (4xx, 5xx)
            # but we can add a check for data if needed, though often an empty response.data on UPDATE is normal.
            if hasattr(response, 'error') and response.error:
                 app.logger.error(f"Supabase API error updating bulletin {id}: {response.error}")
                 flash(f"Database update failed: {response.error.message}", "danger")
                 return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

            flash("Bulletin updated successfully!", "success")
            return redirect(url_for("admin_bulletins"))
        except Exception as e:
            app.logger.error(f"Error updating bulletin {id} in DB: {type(e).__name__} - {str(e)}")
            flash(f"An unexpected error occurred while updating the bulletin: {str(e)}", "danger")
            return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

    # For GET request
    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)


@app.route("/admin/bulletins/delete/<int:id>", methods=["POST"])
@login_required
def admin_delete_bulletin(id):
    # Fetch the bulletin to get its image_url before deleting
    resp = supabase.table("bulletin_posts").select("image_url").eq("id", id).single().execute()
    bulletin_data = resp.data

    if bulletin_data and bulletin_data.get("image_url"):
        delete_from_supabase_storage(bulletin_data["image_url"], "bulletin-images")

    supabase.table("bulletin_posts").delete().eq("id", id).execute()
    flash("Bulletin deleted successfully!", "success")
    return redirect(url_for("admin_bulletins"))


# News Management

@app.route("/admin/news")
@login_required
def admin_news():
    news_resp = supabase.table("news_posts").select("*, image_url").order("date_posted", desc=True).execute()
    news_items = news_resp.data or []
    return render_template("admin/news/index.html", news_items=news_items)


@app.route("/admin/news/create", methods=["GET", "POST"])
@login_required
def admin_create_news():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        image_url = None

        if image_file and image_file.filename: # Check if a file was provided
            image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if image_url is None: # Check if upload failed
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/news/create.html")
        else:
            image_url = None

        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
        }
        if image_url: # Only include image_url if it's not None
            data["image_url"] = image_url

        supabase.table("news_posts").insert(data).execute()

        flash("News item created successfully!", "success")
        return redirect(url_for("admin_news"))

    return render_template("admin/news/create.html")


@app.route("/admin/news/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_news(id):
    try:
        resp = supabase.table("news_posts").select("*, image_url").eq("id", id).single().execute()
        news_from_db = resp.data
    except Exception as e:
        app.logger.error(f"Error fetching news item id {id} for edit: {type(e).__name__} - {str(e)}")
        flash(f"An error occurred while fetching news item details.", "danger")
        return redirect(url_for("admin_news"))

    if not news_from_db:
        flash("News item not found", "danger")
        return redirect(url_for("admin_news"))

    form_data_for_template = news_from_db.copy() # Use a copy for form data

    if request.method == "POST":
        form_data_for_template["title"] = request.form.get("title")
        form_data_for_template["content"] = request.form.get("content")
        form_data_for_template["is_active"] = bool(request.form.get("is_active"))

        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"

        current_db_image_url = news_from_db.get("image_url")
        new_image_url_to_set = current_db_image_url

        # Image handling logic
        if remove_image:
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "news-and-events-images"):
                    flash("Failed to delete the current image. Item not updated.", "danger")
                    return render_template("admin/news/edit.html", news=form_data_for_template)
                new_image_url_to_set = None
            else: # No image to remove
                new_image_url_to_set = None
        elif image_file and image_file.filename: # Check filename to ensure a file was actually uploaded
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "news-and-events-images"):
                    flash("Failed to delete old image before uploading new. Item not updated.", "danger")
                    return render_template("admin/news/edit.html", news=form_data_for_template)

            uploaded_image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if not uploaded_image_url:
                flash("New image upload failed. Item not updated.", "danger")
                return render_template("admin/news/edit.html", news=form_data_for_template)
            new_image_url_to_set = uploaded_image_url

        form_data_for_template["image_url"] = new_image_url_to_set

        # Prepare data for DB update
        update_data_for_db = {
            "title": form_data_for_template["title"],
            "content": form_data_for_template["content"],
            "is_active": form_data_for_template["is_active"],
        }

        if new_image_url_to_set != current_db_image_url:
            update_data_for_db["image_url"] = new_image_url_to_set

        # Database operation
        if not update_data_for_db and new_image_url_to_set == current_db_image_url: # Check if there's anything to update
             flash("No changes detected.", "info")
             return redirect(url_for("admin_news"))

        try:
            response = supabase.table("news_posts").update(update_data_for_db).eq("id", id).execute()
            if hasattr(response, 'error') and response.error:
                 app.logger.error(f"Supabase API error updating news item {id}: {response.error}")
                 flash(f"Database update failed: {response.error.message}", "danger")
                 return render_template("admin/news/edit.html", news=form_data_for_template)

            flash("News & Events updated successfully!", "success")
            return redirect(url_for("admin_news"))
        except Exception as e:
            app.logger.error(f"Error updating news item {id} in DB: {type(e).__name__} - {str(e)}")
            flash(f"An unexpected error occurred while updating the news item: {str(e)}", "danger")
            return render_template("admin/news/edit.html", news=form_data_for_template)

    # For GET request
    return render_template("admin/news/edit.html", news=form_data_for_template)


@app.route("/admin/news/delete/<int:id>", methods=["POST"])
@login_required
def admin_delete_news(id):
    # Fetch the news item to get its image_url before deleting
    resp = supabase.table("news_posts").select("image_url").eq("id", id).single().execute()
    news_data = resp.data

    if news_data and news_data.get("image_url"):
        delete_from_supabase_storage(news_data["image_url"], "news-and-events-images")

    supabase.table("news_posts").delete().eq("id", id).execute()
    flash("News item deleted successfully!", "success")
    return redirect(url_for("admin_news"))


# Patch Notes API Endpoints
@app.route("/api/patch-notes", methods=["GET"])
@login_required # Assuming only logged-in admins should access this, adjust if needed
def get_all_patch_notes():
    try:
        response = supabase.table("patch_notes").select("*").order("date", desc=True).execute()
        if response.data:
            return jsonify(response.data)
        else:
            app.logger.error(f"Error fetching all patch notes: {getattr(response, 'error', 'Unknown error')}")
            return jsonify({"error": "Failed to fetch patch notes", "details": str(getattr(response, 'error', '')) }), 500
    except Exception as e:
        app.logger.error(f"Exception in get_all_patch_notes: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/api/patch-notes/latest", methods=["GET"])
@login_required # Assuming only logged-in admins should access this
def get_latest_patch_note():
    try:
        response = supabase.table("patch_notes").select("*").order("date", desc=True).limit(1).single().execute()
        if response.data:
            return jsonify(response.data)
        else:
            # .single() returns an error in response.error if not found or multiple found
            # However, PostgrestAPIError might be raised before this for other issues.
            # If data is empty and no error object, it means no records found which is not an "error" state.
            if getattr(response, 'error', None):
                 app.logger.error(f"Error fetching latest patch note: {response.error}")
                 return jsonify({"error": "Failed to fetch latest patch note", "details": str(response.error)}), 500
            return jsonify(None), 200 # Return 200 with null body if no patch note exists
    except Exception as e:
        # Handle cases where .single() might raise an exception if data is not a single row (though limit(1) should prevent this)
        # or other unexpected errors.
        app.logger.error(f"Exception in get_latest_patch_note: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

# System Maintenance API Endpoints
@app.route("/api/system-maintenance", methods=["GET"])
@login_required # Admin may want to see all messages, including past ones.
def get_all_system_maintenance():
    try:
        response = supabase.table("system_maintenance").select("*").order("start_time", desc=True).execute()
        if response.data:
            return jsonify(response.data)
        else:
            app.logger.error(f"Error fetching all system maintenance: {getattr(response, 'error', 'Unknown error')}")
            return jsonify({"error": "Failed to fetch system maintenance messages", "details": str(getattr(response, 'error', '')) }), 500
    except Exception as e:
        app.logger.error(f"Exception in get_all_system_maintenance: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/api/system-maintenance/latest", methods=["GET"])
# No login_required, this is for public consumption
def get_latest_system_maintenance():
    try:
        now = datetime.now(pytz.utc).isoformat() # Ensure timezone aware comparison
        # Fetches the most recent maintenance message that is currently active or upcoming.
        # Orders by start_time descending to get the latest if multiple fit criteria.
        response = (
            supabase.table("system_maintenance")
            .select("*")
            .lte("start_time", now) # Maintenance should have started
            .gte("end_time", now)   # And not yet ended
            .order("start_time", desc=True)
            .limit(1)
            .single()
            .execute()
        )

        # If no active maintenance, try to find the next upcoming one
        if not response.data and not getattr(response, 'error', None):
            response = (
                supabase.table("system_maintenance")
                .select("*")
                .gt("start_time", now) # Future start time
                .order("start_time", desc=False) # Ascending to get the soonest
                .limit(1)
                .single()
                .execute()
            )

        if response.data:
            return jsonify(response.data)
        else:
            if getattr(response, 'error', None):
                app.logger.error(f"Error fetching latest system maintenance: {response.error}")
                # Distinguish between "not found" and actual errors if possible,
                # but .single() can make this tricky. For now, a generic error.
                return jsonify({"error": "Failed to fetch latest system maintenance", "details": str(response.error)}), 500
            return jsonify(None), 200 # Return 200 with null body if no relevant maintenance message exists

    except Exception as e:
        app.logger.error(f"Exception in get_latest_system_maintenance: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/api/notifications/recent-unread")
@login_required
def api_recent_unread_notifications():
    try:
        resp = (
            supabase.table("notifications")
            .select("id, form_type, created_at")
            .eq("is_read", False)
            .order("created_at", desc=True)
            .limit(5) # Fetch up to 5 recent unread notifications
            .execute()
        )
        return jsonify(resp.data or [])
    except Exception as e:
        app.logger.error(f"Error fetching recent unread notifications for API: {type(e).__name__} - {str(e)}")
        return jsonify({"error": "Failed to fetch recent notifications", "details": str(e)}), 500

from datetime import timedelta # Ensure timedelta is imported

@app.route("/api/charts/post-activity", methods=["GET"])
@login_required
def chart_post_activity():
    post_type = request.args.get("type", "bulletin")  # bulletin or news
    period = request.args.get("period", "monthly")  # daily, weekly, monthly, yearly, all

    table_name = ""
    if post_type == "bulletin":
        table_name = "bulletin_posts"
    elif post_type == "news":
        table_name = "news_posts"
    else:
        return jsonify({"error": "Invalid post type specified"}), 400

    labels = []
    data_counts = []
    manila_tz = pytz.timezone("Asia/Manila") # Use Manila timezone for display

    try:
        now_local = datetime.now(manila_tz) # Use localized 'now' for period calculations

        if period == "daily": # Last 30 days
            counts_by_day = {}
            # Initialize labels for the last 30 days ending today (local time)
            for i in range(30):
                day = (now_local - timedelta(days=29) + timedelta(days=i))
                counts_by_day[day.strftime("%Y-%m-%d")] = 0

            # Fetch data within this range (adjust to UTC for query if DB stores UTC)
            # Assuming date_posted is stored as TIMESTAMPTZ (UTC)
            start_utc = (now_local - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.utc)
            end_utc = now_local.astimezone(pytz.utc) # Use current time as end for today

            raw_data = supabase.table(table_name)\
                .select("id, date_posted")\
                .gte("date_posted", start_utc.isoformat())\
                .lte("date_posted", end_utc.isoformat())\
                .execute().data or []

            for item in raw_data:
                # Convert UTC from DB to local time for correct day grouping
                item_date_local = parser.isoparse(item['date_posted']).astimezone(manila_tz)
                day_str = item_date_local.strftime("%Y-%m-%d")
                if day_str in counts_by_day:
                    counts_by_day[day_str] += 1

            sorted_days_keys = sorted(counts_by_day.keys())
            labels = [datetime.strptime(d, "%Y-%m-%d").strftime("%b %d") for d in sorted_days_keys]
            data_counts = [counts_by_day[d] for d in sorted_days_keys]

        elif period == "weekly": # Last 12 weeks
            counts_by_week_start = {}
            # Initialize labels for the last 12 weeks, week starting on Monday
            for i in range(12):
                current_week_day = (now_local - timedelta(weeks=11-i))
                monday_of_that_week = current_week_day - timedelta(days=current_week_day.weekday())
                counts_by_week_start[monday_of_that_week.strftime("%Y-%m-%d")] = 0

            # Define the UTC range for the query
            # Start from 12 weeks ago (from Monday of that week)
            query_start_date_local = (now_local - timedelta(weeks=11)) - timedelta(days=now_local.weekday())
            start_utc = query_start_date_local.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(pytz.utc)
            end_utc = now_local.astimezone(pytz.utc)


            raw_data = supabase.table(table_name)\
                .select("id, date_posted")\
                .gte("date_posted", start_utc.isoformat())\
                .lte("date_posted", end_utc.isoformat())\
                .execute().data or []

            for item in raw_data:
                item_date_local = parser.isoparse(item['date_posted']).astimezone(manila_tz)
                monday_of_item_week = item_date_local - timedelta(days=item_date_local.weekday())
                week_key = monday_of_item_week.strftime("%Y-%m-%d")
                if week_key in counts_by_week_start: # Only count if the week_key is one we initialized
                     counts_by_week_start[week_key] += 1

            sorted_week_keys = sorted(counts_by_week_start.keys())
            labels = [datetime.strptime(w, "%Y-%m-%d").strftime("Wk %U (%b %d)") for w in sorted_week_keys] # Week number and date
            data_counts = [counts_by_week_start[w] for w in sorted_week_keys]

        elif period == "monthly" or period == "all":
            limit_months = 12 if period == "monthly" else None

            all_db_data = supabase.table(table_name).select("id, date_posted").order("date_posted", desc=False).execute().data or []

            counts_by_month_start = {}
            if all_db_data:
                # Determine range of months based on actual data, converted to local time
                # Ensure first_item_date_local and last_item_date_local are timezone-aware before comparison
                first_item_date_local = parser.isoparse(all_db_data[0]['date_posted']).astimezone(manila_tz)
                last_item_date_local = parser.isoparse(all_db_data[-1]['date_posted']).astimezone(manila_tz)

                current_month_iterator = first_item_date_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                # Ensure end_iterate_month is also timezone-aware and correctly represents the start of its month
                end_iterate_month = last_item_date_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


                while current_month_iterator <= end_iterate_month:
                    counts_by_month_start[current_month_iterator.strftime("%Y-%m-01")] = 0
                    if current_month_iterator.month == 12:
                        current_month_iterator = current_month_iterator.replace(year=current_month_iterator.year + 1, month=1)
                    else:
                        current_month_iterator = current_month_iterator.replace(month=current_month_iterator.month + 1)

                for item in all_db_data:
                    item_date_local = parser.isoparse(item['date_posted']).astimezone(manila_tz)
                    month_key = item_date_local.strftime("%Y-%m-01") # Group by start of month in local time
                    if month_key in counts_by_month_start:
                        counts_by_month_start[month_key] += 1

            sorted_month_keys = sorted(counts_by_month_start.keys())

            if period == "monthly" and limit_months:
                # Ensure we only take the last N months if there are more than N months of data
                relevant_month_keys = sorted_month_keys[-limit_months:]
            else: # 'all'
                relevant_month_keys = sorted_month_keys

            labels = [datetime.strptime(m_key, "%Y-%m-%d").astimezone(manila_tz).strftime("%b %Y") for m_key in relevant_month_keys]
            data_counts = [counts_by_month_start.get(m_key, 0) for m_key in relevant_month_keys]


        elif period == "yearly":
            all_db_data = supabase.table(table_name).select("id, date_posted").order("date_posted", desc=False).execute().data or []
            counts_by_year = {}
            if all_db_data:
                # Convert to local timezone first before extracting year
                first_year_local = parser.isoparse(all_db_data[0]['date_posted']).astimezone(manila_tz).year
                last_year_local = parser.isoparse(all_db_data[-1]['date_posted']).astimezone(manila_tz).year
                for year_val in range(first_year_local, last_year_local + 1):
                    counts_by_year[str(year_val)] = 0 # Key is string year

                for item in all_db_data:
                    item_year_local_str = parser.isoparse(item['date_posted']).astimezone(manila_tz).strftime("%Y")
                    if item_year_local_str in counts_by_year:
                        counts_by_year[item_year_local_str] += 1

            sorted_year_keys = sorted(counts_by_year.keys())
            labels = sorted_year_keys # Years as strings
            data_counts = [counts_by_year.get(y_key, 0) for y_key in sorted_year_keys]

        else:
            return jsonify({"error": "Invalid period specified"}), 400

        return jsonify({"labels": labels, "datasets": [{"label": f"{post_type.title()} Posts", "data": data_counts, "borderColor": "#0e6ba8", "tension": 0.1}]})

    except Exception as e:
        app.logger.error(f"Error generating chart data for {post_type} ({period}): {type(e).__name__} - {str(e)}")
        app.logger.error(traceback.format_exc()) # Log full traceback
        return jsonify({"error": "Failed to generate chart data", "details": str(e)}), 500

# System Maintenance CRUD
@app.route("/admin/maintenance")
@login_required
def admin_maintenance_list():
    try:
        resp = supabase.table("system_maintenance").select("*").order("start_time", desc=True).execute()
        maintenance_messages = resp.data or []
    except Exception as e:
        app.logger.error(f"Error fetching system maintenance list: {e}")
        flash("Failed to load maintenance messages.", "danger")
        maintenance_messages = []
    return render_template("admin/maintenance/list.html", maintenance_messages=maintenance_messages)

@app.route("/admin/maintenance/create", methods=["GET", "POST"])
@login_required
def admin_maintenance_create():
    if request.method == "POST":
        title = request.form.get("title")
        message = request.form.get("message")
        start_time_str = request.form.get("start_time")
        end_time_str = request.form.get("end_time")

        try:
            # Convert to datetime objects, assuming UTC or that Supabase handles timezone from ISO string
            # The HTML datetime-local input provides ISO-like format "YYYY-MM-DDTHH:MM"
            # Ensure these are parsed correctly and are timezone-aware if necessary for Supabase.
            # For Supabase TIMESTAMPTZ, ISO 8601 strings are generally fine.
            start_time_dt = parser.isoparse(start_time_str) if start_time_str else None
            end_time_dt = parser.isoparse(end_time_str) if end_time_str else None

            if not title or not message or not start_time_dt or not end_time_dt:
                flash("All fields are required.", "danger")
                return render_template("admin/maintenance/form.html", form_action="Create")

            data = {
                "title": title,
                "message": message,
                "start_time": start_time_dt.isoformat(),
                "end_time": end_time_dt.isoformat(),
                "created_by": current_user.id
            }
            supabase.table("system_maintenance").insert(data).execute()
            flash("System maintenance message created successfully!", "success")
            return redirect(url_for("admin_maintenance_list"))
        except Exception as e:
            app.logger.error(f"Error creating system maintenance message: {e}")
            flash(f"Failed to create message: {str(e)}", "danger")

    return render_template("admin/maintenance/form.html", form_action="Create", maintenance={})


@app.route("/admin/maintenance/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_maintenance_edit(id):
    try:
        resp = supabase.table("system_maintenance").select("*").eq("id", id).single().execute()
        maintenance_message = resp.data
        if not maintenance_message:
            flash("Maintenance message not found.", "danger")
            return redirect(url_for("admin_maintenance_list"))
    except Exception as e:
        app.logger.error(f"Error fetching maintenance message for edit (id: {id}): {e}")
        flash("Failed to load maintenance message for editing.", "danger")
        return redirect(url_for("admin_maintenance_list"))

    if request.method == "POST":
        title = request.form.get("title")
        message = request.form.get("message")
        start_time_str = request.form.get("start_time")
        end_time_str = request.form.get("end_time")
        try:
            start_time_dt = parser.isoparse(start_time_str) if start_time_str else None
            end_time_dt = parser.isoparse(end_time_str) if end_time_str else None

            if not title or not message or not start_time_dt or not end_time_dt:
                flash("All fields are required.", "danger")
                # Pass current data back to form
                maintenance_message.update(request.form.to_dict()) # Update with current form values for repopulation
                return render_template("admin/maintenance/form.html", form_action="Edit", maintenance=maintenance_message)

            update_data = {
                "title": title,
                "message": message,
                "start_time": start_time_dt.isoformat(),
                "end_time": end_time_dt.isoformat(),
                # "updated_by": current_user.id # If you have an updated_by field
                # "updated_at": datetime.now(pytz.utc).isoformat() # If you have an updated_at field
            }
            supabase.table("system_maintenance").update(update_data).eq("id", id).execute()
            flash("System maintenance message updated successfully!", "success")
            return redirect(url_for("admin_maintenance_list"))
        except Exception as e:
            app.logger.error(f"Error updating system maintenance message (id: {id}): {e}")
            flash(f"Failed to update message: {str(e)}", "danger")
            maintenance_message.update(request.form.to_dict()) # Update with current form values for repopulation
            return render_template("admin/maintenance/form.html", form_action="Edit", maintenance=maintenance_message)

    # For GET request, ensure times are formatted for datetime-local input
    # The value attribute of <input type="datetime-local"> needs "YYYY-MM-DDTHH:MM"
    if maintenance_message.get("start_time"):
        maintenance_message["start_time_form"] = parser.isoparse(maintenance_message["start_time"]).strftime("%Y-%m-%dT%H:%M")
    if maintenance_message.get("end_time"):
        maintenance_message["end_time_form"] = parser.isoparse(maintenance_message["end_time"]).strftime("%Y-%m-%dT%H:%M")

    return render_template("admin/maintenance/form.html", form_action="Edit", maintenance=maintenance_message)

@app.route("/admin/maintenance/delete/<int:id>", methods=["POST"])
@login_required
def admin_maintenance_delete(id):
    try:
        supabase.table("system_maintenance").delete().eq("id", id).execute()
        flash("System maintenance message deleted successfully!", "success")
    except Exception as e:
        app.logger.error(f"Error deleting system maintenance message (id: {id}): {e}")
        flash("Failed to delete message.", "danger")
    return redirect(url_for("admin_maintenance_list"))

# Setup initial admin user

@app.route("/setup", methods=["GET", "POST"])
def setup():
    users_resp = supabase.table("users").select("id").limit(1).execute()
    if users_resp.data and len(users_resp.data) > 0:
        flash("Setup has already been completed", "warning")
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        name = request.form.get("name")

        if password != confirm_password:
            flash("Passwords do not match", "danger")
            return redirect(url_for("setup"))

        password_hash = generate_password_hash(password)

        data = {
            "username": username,
            "password_hash": password_hash,
            "name": name,
            "role": "admin",
        }

        supabase.table("users").insert(data).execute()

        flash("Initial setup completed. You can now log in.", "success")
        return redirect(url_for("admin_login"))

    return render_template("admin/setup.html")


@app.errorhandler(500)
def internal_error(error):
    return f"500 error: {error}", 500

@app.template_filter("datetimeformat")
def datetimeformat(value, format="%B %d, %Y %I:%M %p"):
    """
    Convert an ISO‑8601 string or datetime into Asia/Manila time,
    then format it for display.
    """
    manila = pytz.timezone("Asia/Manila")
    # Accept either str or datetime
    if isinstance(value, str):
        dt = parser.isoparse(value)
    else:
        dt = value
    # Ensure timezone‑aware, then convert
    if dt.tzinfo is None:
        dt = manila.localize(dt)
    dt = dt.astimezone(manila)
    return dt.strftime(format)

@app.route("/credits")
def credit():
    return render_template("credits.html")

@app.route("/about")
def about():
    return render_template("about.html")
#@app.route("/credits/alden_richards")
#def alden_richards():
    #return  render_template("alden.html")

@app.route("/coming_soon")
def coming_soon():
    return render_template("coming_soon.html")

@app.route("/admin/brgy_certificate_requests")
@login_required
def brgy_certificate_requests():
    return render_template("admin/brgy_certificate_requests.html")

@app.route("/admin/business_permit_requests")
@login_required
def business_permit_requests():
    return render_template("admin/business_permit_requests.html")

@app.route("/admin/reports_and_concerns")
@login_required
def reports_and_concerns():
    return render_template("admin/reports_and_concerns.html")

# --- Google Form Notification Webhook ---
@app.route("/api/notifications/google-form", methods=["POST"])
def google_form_notification():
    # Basic security: Check for a secret key in the request headers or payload
    # For this example, we'll expect it in the JSON payload from Google Apps Script
    # In a production app, consider using request headers for the secret key.

    # Get the API secret key from environment variables for better security
    EXPECTED_API_SECRET_KEY = os.getenv("GOOGLE_APPS_SCRIPT_SECRET_KEY")
    if not EXPECTED_API_SECRET_KEY:
        app.logger.error("GOOGLE_APPS_SCRIPT_SECRET_KEY is not set in environment variables.")
        return jsonify({"error": "Server configuration error"}), 500

    try:
        payload = request.get_json()
        if not payload:
            app.logger.warning("Webhook: Received empty payload.")
            return jsonify({"error": "Empty payload"}), 400

        submitted_secret_key = payload.get("secret_key")
        if submitted_secret_key != EXPECTED_API_SECRET_KEY:
            app.logger.warning(f"Webhook: Invalid secret key. Submitted: {submitted_secret_key}")
            return jsonify({"error": "Unauthorized"}), 403

        form_type = payload.get("form_type")
        submission_timestamp_str = payload.get("submission_timestamp")
        form_data = payload.get("data")

        if not form_type or not form_data:
            app.logger.warning(f"Webhook: Missing form_type or data in payload. Form Type: {form_type}")
            return jsonify({"error": "Missing required fields: form_type, data"}), 400

        # Attempt to parse the submission_timestamp
        # The Google Apps Script sends it as a string, potentially localized.
        # We'll try to parse it; if it fails, we'll default to None, and Supabase will use its default.
        created_at_dt = None
        if submission_timestamp_str:
            try:
                # Using dateutil.parser which is quite flexible
                created_at_dt = parser.isoparse(submission_timestamp_str)
                # Ensure it's timezone-aware, defaulting to UTC if not specified
                if created_at_dt.tzinfo is None:
                    created_at_dt = pytz.utc.localize(created_at_dt)
            except ValueError:
                app.logger.warning(f"Webhook: Could not parse submission_timestamp '{submission_timestamp_str}'. Will use current time for 'created_at'.")
                # If parsing fails, created_at_dt remains None, Supabase will use its default for created_at if the column definition allows
                # However, our table has `created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL`.
                # If we want to store the original string when parsing fails, the table schema needs a different column.
                # For now, if parsing fails, Supabase's DEFAULT NOW() for `created_at` will be used.
                # `received_at` will always be NOW() by the database.

        notification_data_to_insert = {
            "form_type": form_type,
            "data": form_data, # This should be the e.namedValues from Google Apps Script
            "is_read": False,
            # If created_at_dt is successfully parsed, use it. Otherwise, Supabase default will apply.
            # Supabase client might require an ISO format string for timestamps.
        }
        if created_at_dt:
            notification_data_to_insert["created_at"] = created_at_dt.isoformat()

        # `received_at` will be set by the database default (NOW())

        app.logger.info(f"Webhook: Received valid notification for form_type: {form_type}. Inserting into Supabase.")

        try:
            insert_response = supabase.table("notifications").insert(notification_data_to_insert).execute()

            # Check if the insert was successful (Supabase typically returns data on success)
            if hasattr(insert_response, 'data') and insert_response.data:
                app.logger.info(f"Webhook: Notification successfully inserted. Response: {insert_response.data}")
                return jsonify({"message": "Notification received and stored successfully", "id": insert_response.data[0].get('id')}), 201
            elif hasattr(insert_response, 'error') and insert_response.error:
                app.logger.error(f"Webhook: Supabase insert error: {insert_response.error}")
                return jsonify({"error": "Failed to store notification in database", "details": str(insert_response.error)}), 500
            else:
                app.logger.error(f"Webhook: Supabase insert failed with no specific error data. Response: {insert_response}")
                return jsonify({"error": "Failed to store notification in database, unknown reason"}), 500

        except Exception as db_e:
            app.logger.error(f"Webhook: Database exception during insert: {type(db_e).__name__} - {str(db_e)}")
            app.logger.error(traceback.format_exc())
            return jsonify({"error": "Database operation failed"}), 500

    except Exception as e:
        app.logger.error(f"Webhook: General exception: {type(e).__name__} - {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An unexpected error occurred on the server"}), 500


if __name__ == "__main__":
    # Use 0.0.0.0 to be reachable in local network, change debug to False in production
    app.run(host="0.0.0.0", debug=True)#
