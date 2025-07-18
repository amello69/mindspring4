import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import bcrypt
import json
import openai
import uuid
import base64 # Import base64 for decoding
import os # Import os for environment variables
from pypdf import PdfReader # Import PdfReader for reading PDF files
from gtts import gTTS # Import gTTS for Text-to-Speech
import io # Import io for handling in-memory audio files
import requests # Import requests for making HTTP calls (though no longer directly used for DALL-E)

# --- Firebase Initialization ---
# Check if Firebase app is already initialized to prevent re-initialization errors
# Use firebase_admin._apps to check if any app is already initialized
if not firebase_admin._apps:
    try:
        # Attempt to get the Base64 encoded Firebase service account key from environment variables
        firebase_service_account_key_b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_B64")

        if firebase_service_account_key_b64:
            # Decode the Base64 string back to JSON content
            firebase_service_account_key_str = base64.b64decode(firebase_service_account_key_b64).decode('utf-8')
            
            # Initialize Firebase Admin SDK
            cred = credentials.Certificate(json.loads(firebase_service_account_key_str))
            firebase_admin.initialize_app(cred)
            
            st.session_state.firebase_initialized = True
            st.success("Firebase initialized successfully!")
            # No rerun needed here, as it's part of the initial load
        else:
            st.warning("Firebase service account key (FIREBASE_SERVICE_ACCOUNT_KEY_B64) not found in environment variables. Please set it securely.")
            st.session_state.firebase_initialized = False
    except Exception as e:
        st.error(f"Error initializing Firebase from environment variable: {e}")
        st.session_state.firebase_initialized = False
else:
    # If an app is already initialized, set the session state flag to True
    st.session_state.firebase_initialized = True
    # st.info("Firebase app already initialized.") # Optional: for debugging

# Ensure db client is available if Firebase initialized
if st.session_state.firebase_initialized:
    db = firestore.client()
else:
    db = None # db will be None until Firebase is initialized

# --- OpenAI API Key Setup ---
try:
    openai_api_key = st.secrets["OPENAI_API_KEY"]
    st.session_state.openai_initialized = True
except KeyError:
    st.error("OpenAI API key not found in Streamlit secrets. Please add it.")
    st.session_state.openai_initialized = False

# --- Session State Initialization ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'login'
if 'username' not in st.session_state:
    st.session_state.username = None
if 'user_data' not in st.session_state:
    st.session_state.user_data = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'current_study_subject' not in st.session_state:
    st.session_state.current_study_subject = None
if 'subject_context_loaded' not in st.session_state:
    st.session_state.subject_context_loaded = False
if 'active_syllabus' not in st.session_state:
    st.session_state.active_syllabus = ""
if 'active_subject_context' not in st.session_state:
    st.session_state.active_subject_context = ""
if 'generating_image' not in st.session_state:
    st.session_state.generating_image = False


# --- Helper Functions ---

def hash_password(password):
    """Hashes a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed_password):
    """Checks if a password matches a hashed password."""
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_user_doc_ref(username):
    """Returns the Firestore document reference for a given username."""
    # Ensure db is not None before attempting to access it
    if db:
        return db.collection('users').document(username)
    else:
        return None # Or raise an error, depending on desired behavior

def load_user_data(username):
    """Loads user data from Firestore."""
    doc_ref = get_user_doc_ref(username)
    if doc_ref: # Check if doc_ref is valid
        user_doc = doc_ref.get()
        if user_doc.exists:
            st.session_state.user_data = user_doc.to_dict()
            st.session_state.chat_history = st.session_state.user_data.get('chat_history', [])
            return True
    return False

def update_user_data(data):
    """Updates user data in Firestore."""
    if st.session_state.username and st.session_state.user_data and db: # Ensure db is initialized
        doc_ref = get_user_doc_ref(st.session_state.username)
        if doc_ref: # Check if doc_ref is valid
            doc_ref.set(data, merge=True) # Use merge=True to update specific fields
            st.session_state.user_data = data # Update session state immediately
            return True
    return False

def save_chat_history():
    """Saves the current chat history to Firestore."""
    if st.session_state.username and st.session_state.user_data and db: # Ensure db is initialized
        doc_ref = get_user_doc_ref(st.session_state.username)
        if doc_ref: # Check if doc_ref is valid
            doc_ref.update({'chat_history': st.session_state.chat_history})

# Function to read text from a PDF file
def read_pdf_text(file_path):
    """Reads text content from a PDF file."""
    text_content = ""
    print(f"DEBUG: Attempting to read PDF: {file_path}") # Debug print
    try:
        reader = PdfReader(file_path)
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
        print(f"DEBUG: Successfully read PDF: {file_path}, content length: {len(text_content)}") # Debug print
    except FileNotFoundError:
        print(f"ERROR: PDF file not found: {file_path}") # Debug print
        st.error(f"PDF file not found: {file_path}")
        return None
    except Exception as e:
        print(f"ERROR: Error reading PDF file {file_path}: {e}") # Debug print
        st.error(f"Error reading PDF file {file_path}: {e}")
        return None
    return text_content

# Function to read text from a plain text file
def read_text_file(file_path):
    """Reads text content from a plain text file."""
    text_content = ""
    print(f"DEBUG: Attempting to read TXT: {file_path}") # Debug print
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text_content = f.read()
        print(f"DEBUG: Successfully read TXT: {file_path}, content length: {len(text_content)}") # Debug print
    except FileNotFoundError:
        print(f"ERROR: Text file not found: {file_path}") # Debug print
        st.error(f"Text file not found: {file_path}")
        return None
    except Exception as e:
        print(f"ERROR: Error reading text file {file_path}: {e}") # Debug print
        st.error(f"Error reading text file {file_path}: {e}")
        return None
    return text_content

# Function for Text-to-Speech
def text_to_speech(text):
    """Converts text to speech and returns audio bytes."""
    try:
        tts = gTTS(text=text, lang='en', slow=False)
        fp = io.BytesIO()
        tts.save(fp)
        fp.seek(0)
        return fp.read()
    except Exception as e:
        st.error(f"Error converting text to speech: {e}")
        return None

# Function to generate image using DALL-E API
def generate_image(prompt):
    """Generates an image using the OpenAI DALL-E 3 API."""
    st.session_state.generating_image = True
    print(f"DEBUG: Starting DALL-E image generation for prompt: {prompt}")
    
    # Create a placeholder for status messages during image generation
    status_placeholder = st.empty()
    status_placeholder.info("Starting DALL-E image generation...")

    try:
        client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"]) # Use the already initialized OpenAI client

        status_placeholder.info("Sending request to DALL-E API...")
        response = client.images.generate(
            model="dall-e-3",  # Specify DALL-E 3 model
            prompt=prompt,
            size="1024x1024", # Standard size
            quality="standard", # Standard quality for cost efficiency
            n=1 # Generate one image
        )
        
        # DALL-E API returns a list of image objects, each with a URL
        if response.data and len(response.data) > 0 and response.data[0].url:
            image_url = response.data[0].url
            print(f"DEBUG: Successfully generated DALL-E image. Image URL: {image_url}")
            status_placeholder.success("Image generated successfully!")
            return image_url
        else:
            # Add more specific logging for when URL is not found
            print(f"ERROR: DALL-E image generation failed: No image URL found in response. Full response data: {response.data}")
            status_placeholder.error("DALL-E image generation failed: No image data returned. Check Streamlit Cloud logs for full response details.")
            return None
    except openai.APIError as e:
        print(f"ERROR: OpenAI DALL-E API error: {e}")
        status_placeholder.error(f"OpenAI DALL-E API error: {e}. Please check your API key, permissions, and DALL-E quota. See Streamlit Cloud logs for more details.")
        return None
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during DALL-E image generation: {e}")
        status_placeholder.error(f"An unexpected error occurred during DALL-E image generation: {e}. Check Streamlit Cloud logs for details.")
        return None
    finally:
        st.session_state.generating_image = False
        print("DEBUG: Finished DALL-E image generation attempt.")


# --- Pages ---

def login_page():
    """Displays the login page."""
    st.title("AI Tutor Platform - Login")

    # Only show login/register forms if Firebase is initialized
    if not st.session_state.firebase_initialized:
        st.warning("Firebase is not initialized. Please ensure FIREBASE_SERVICE_ACCOUNT_KEY_B64 is set in Streamlit Cloud environment variables.")
        return

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        # Submit button for the login form
        submit_button = st.form_submit_button("Login")

        if submit_button:
            if not st.session_state.firebase_initialized:
                st.error("Firebase is not initialized. Cannot log in.")
                return

            user_doc_ref = get_user_doc_ref(username)
            if user_doc_ref is None:
                st.error("Firebase is not properly configured. Cannot access user data.")
                return

            user_doc = user_doc_ref.get()

            if user_doc.exists:
                user_data = user_doc.to_dict()
                if check_password(password, user_data['password_hash']):
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.session_state.user_data = user_data
                    st.session_state.chat_history = user_data.get('chat_history', [])
                    st.session_state.current_page = 'tutor' # Redirect to tutor page after login
                    st.rerun()
                else:
                    st.error("Incorrect password.")
            else:
                st.error("Username not found.")

    st.markdown("---")
    st.write("Don't have an account?")
    if st.button("Register Here"):
        st.session_state.current_page = 'register'
        st.rerun()

    st.write("Forgot your password?")
    if st.button("Reset Password"):
        # This would typically link to an external password reset service
        st.info("Password reset functionality is not implemented in this demo. Please contact support.")

def register_page():
    """Displays the user registration page."""
    st.title("AI Tutor Platform - Register")

    if not st.session_state.firebase_initialized:
        st.warning("Firebase is not initialized. Please ensure FIREBASE_SERVICE_ACCOUNT_KEY_B64 is set in Streamlit Cloud environment variables.")
        return

    with st.form("register_form"):
        first_name = st.text_input("First Name")
        last_name = st.text_input("Last Name")
        username = st.text_input("Username")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        confirm_password = st.text_input("Confirm Password", type="password")

        # Placeholder for reCAPTCHA
        st.info("reCAPTCHA integration is typically handled server-side for security. This is a placeholder.")

        # Submit button for the registration form
        submit_button = st.form_submit_button("Register")

        if submit_button:
            if not st.session_state.firebase_initialized:
                st.error("Firebase is not initialized. Cannot register.")
                return

            user_doc_ref = get_user_doc_ref(username)
            if user_doc_ref is None:
                st.error("Firebase is not properly configured. Cannot register user.")
                return

            if password != confirm_password:
                st.error("Passwords do not match.")
            elif not username or not password or not first_name or not last_name or not email:
                st.error("All fields are required.")
            else:
                if user_doc_ref.get().exists:
                    st.error("Username already exists. Please choose a different one.")
                else:
                    hashed_pass = hash_password(password)
                    initial_tokens = 1000
                    user_data = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'email': email,
                        'username': username,
                        'password_hash': hashed_pass,
                        'tokens': initial_tokens,
                        'learning_preferences': {
                            'style': 'interactive',
                            'pace': 'moderate',
                            'difficulty': 'beginner'
                        },
                        'subjects': [],
                        'chat_history': []
                    }
                    user_doc_ref.set(user_data)
                    st.success("Registration successful! You can now log in.")
                    st.session_state.current_page = 'login'
                    st.rerun()

    st.markdown("---")
    st.write("Already have an account?")
    if st.button("Login Here"):
        st.session_state.current_page = 'login'
        st.rerun()

    st.write("Forgot your password?")
    if st.button("Reset Password"):
        # This would typically link to an external password reset service
        st.info("Password reset functionality is not implemented in this demo. Please contact support.")

def profile_page():
    """Displays the user profile page."""
    st.title("User Profile")

    if not st.session_state.logged_in or not st.session_state.user_data:
        st.warning("Please log in to view your profile.")
        st.session_state.current_page = 'login'
        st.rerun()
        return

    if not st.session_state.firebase_initialized:
        st.error("Firebase is not initialized. Please ensure FIREBASE_SERVICE_ACCOUNT_KEY_B64 is set in Streamlit Cloud environment variables.")
        return

    user_data = st.session_state.user_data

    st.header("Personal Information")
    st.write(f"**Username:** {user_data.get('username', 'N/A')}")
    st.write(f"**First Name:** {user_data.get('first_name', 'N/A')}")
    st.write(f"**Last Name:** {user_data.get('last_name', 'N/A')}")
    st.write(f"**Email:** {user_data.get('email', 'N/A')}")
    st.write(f"**Tokens Remaining:** {user_data.get('tokens', 'N/A')}")

    st.header("Password Reset")
    st.info("To reset your password, please contact support or use the 'Forgot Password' link on the login page (if implemented externally).")

    st.header("Learning Preferences")
    current_preferences = user_data.get('learning_preferences', {})
    with st.form("learning_preferences_form"):
        st.subheader("Update Your Learning Preferences")
        
        # Options for learning style
        learning_style_options = ['Interactive', 'Visual', 'Auditory', 'Reading/Writing', 'Kinesthetic']
        current_style = current_preferences.get('style', 'Interactive')
        # Safely get index, default to 0 if current_style is not in options
        style_index = learning_style_options.index(current_style) if current_style in learning_style_options else 0
        learning_style = st.selectbox(
            "Preferred Learning Style:",
            learning_style_options,
            index=style_index
        )
        
        # Options for learning pace
        learning_pace_options = ['Slow', 'Moderate', 'Fast']
        current_pace = current_preferences.get('pace', 'Moderate')
        # Safely get index, default to 0 if current_pace is not in options
        pace_index = learning_pace_options.index(current_pace) if current_pace in learning_pace_options else 0
        learning_pace = st.selectbox(
            "Preferred Learning Pace:",
            learning_pace_options,
            index=pace_index
        )
        
        # Options for difficulty level
        difficulty_level_options = ['Beginner', 'Intermediate', 'Advanced']
        current_difficulty = current_preferences.get('difficulty', 'Beginner')
        # Safely get index, default to 0 if current_difficulty is not in options
        difficulty_index = difficulty_level_options.index(current_difficulty) if current_difficulty in difficulty_level_options else 0
        difficulty_level = st.selectbox(
            "Preferred Difficulty Level:",
            difficulty_level_options,
            index=difficulty_index
        )
        
        # Submit button for learning preferences form
        update_pref_button = st.form_submit_button("Update Preferences")

        if update_pref_button:
            user_data['learning_preferences'] = {
                'style': learning_style,
                'pace': learning_pace,
                'difficulty': difficulty_level
            }
            if update_user_data(user_data):
                st.success("Learning preferences updated successfully!")
            else:
                st.error("Failed to update learning preferences.")

    st.header("Subjects")
    # Updated list of available subjects for general profile (multi-select)
    available_subjects = [
        "English A", "Mathematics", "Biology", "Integrated Science",
        "Agricultural Science", "Chemistry", "Human and Social Biology",
        "Physics", "Social Studies", "Principles of Business", "Geography"
    ]
    current_subjects = user_data.get('subjects', [])

    with st.form("subjects_form"):
        st.subheader("Select Your General Subjects (Max 5)")
        st.info("These are for your general profile. To select a subject for today's study session, go to the 'Tutor' page.")
        
        # Filter current_subjects to ensure only valid subjects are used as default
        filtered_current_subjects = [
            sub for sub in current_subjects if sub in available_subjects
        ]

        selected_subjects = st.multiselect(
            "Choose subjects:",
            available_subjects,
            default=filtered_current_subjects # Use the filtered list here
        )
        # Submit button for subjects form
        update_subjects_button = st.form_submit_button("Update Subjects")

        if update_subjects_button:
            if len(selected_subjects) > 5:
                st.error("You can select a maximum of 5 subjects.")
            else:
                user_data['subjects'] = selected_subjects
                if update_user_data(user_data):
                    st.success("Subjects updated successfully!")
                else:
                    st.error("Failed to update subjects.")

    st.markdown("---")
    if st.button("Go to Tutor Page"):
        st.session_state.current_page = 'tutor'
        st.rerun()

def tutor_page():
    """Displays the AI tutor chat page."""
    st.title("AI Tutor Chat")

    if not st.session_state.logged_in or not st.session_state.user_data:
        st.warning("Please log in to access the tutor.")
        st.session_state.current_page = 'login'
        st.rerun()
        return

    if not st.session_state.openai_initialized:
        st.error("OpenAI API is not initialized. Cannot use tutor.")
        return
    
    if not st.session_state.firebase_initialized:
        st.error("Firebase is not initialized. Please ensure FIREBASE_SERVICE_ACCOUNT_KEY_B64 is set in Streamlit Cloud environment variables.")
        return

    user_data = st.session_state.user_data
    current_tokens = user_data.get('tokens', 0)
    st.sidebar.metric("Tokens Remaining", current_tokens)

    st.sidebar.header("Your Settings")
    st.sidebar.write(f"**Username:** {user_data.get('username', 'N/A')}")
    st.sidebar.write(f"**Learning Style:** {user_data.get('learning_preferences', {}).get('style', 'N/A')}")
    st.sidebar.write(f"**Subjects (Profile):** {', '.join(user_data.get('subjects', ['N/A']))}")
    st.sidebar.write(f"**Current Study Subject:** {st.session_state.current_study_subject if st.session_state.current_study_subject else 'Not selected'}")

    # Moved student_grade definition to the top of tutor_page
    student_grade = st.sidebar.selectbox("Your Grade Level:", ["Elementary", "Middle School", "High School", "College"], index=2) # Default to High School


    # Define the full list of available subjects for study
    available_study_subjects = [
        "English A", "Mathematics", "Biology", "Integrated Science",
        "Agricultural Science", "Chemistry", "Human and Social Biology",
        "Physics", "Social Studies", "Principles of Business", "Geography"
    ]

    # --- Subject Selection for Today's Study Session ---
    # Only show this block if a subject hasn't been selected or context not loaded
    if not st.session_state.current_study_subject or not st.session_state.subject_context_loaded:
        st.subheader("Which subject do you want to study today?")
        with st.form("study_subject_form"):
            selected_subject_for_session = st.selectbox(
                "Select a subject:",
                ["-- Select a Subject --"] + available_study_subjects,
                key="study_subject_selector"
            )
            start_session_button = st.form_submit_button("Start Study Session")

            if start_session_button: # Check if button is clicked
                print(f"DEBUG: 'Start Study Session' button clicked.") # Debug print
                if selected_subject_for_session == "-- Select a Subject --":
                    st.warning("Please select a valid subject to start your study session.")
                    print("DEBUG: Invalid subject selected.") # Debug print
                    st.stop() # Stop execution to show warning
                
                print(f"DEBUG: Selected subject: {selected_subject_for_session}") # Debug print
                st.session_state.current_study_subject = selected_subject_for_session
                
                # Construct file paths for PDF syllabus and TXT context files
                # Replace spaces and slashes for safe file names
                subject_file_name = selected_subject_for_session.replace(" ", "_").replace("/", "-") 
                syllabus_file_path = os.path.join("subject_context", f"syl_{subject_file_name}.pdf")
                context_file_path = os.path.join("subject_context", f"con_{subject_file_name}.txt") # Changed to .txt

                syllabus_content = ""
                context_content = ""

                # Load syllabus file (PDF)
                syllabus_content = read_pdf_text(syllabus_file_path)
                if syllabus_content is None: # read_pdf_text returns None on error
                    print(f"DEBUG: Syllabus content is None. Stopping.") # Debug print
                    st.stop() # Stop execution to show error
                    
                # Load context file (TXT)
                context_content = read_text_file(context_file_path) # Changed to read_text_file
                if context_content is None: # read_text_file returns None on error
                    print(f"DEBUG: Context content is None. Stopping.") # Debug print
                    st.stop() # Stop execution to show error

                st.session_state.active_syllabus = syllabus_content
                st.session_state.active_subject_context = context_content
                st.session_state.subject_context_loaded = True
                print(f"DEBUG: Subject context loaded successfully. current_study_subject: {st.session_state.current_study_subject}, subject_context_loaded: {st.session_state.subject_context_loaded}") # Debug print
                
                # Clear chat history for new subject session
                st.session_state.chat_history = []
                
                # Construct the initial system prompt with all context
                preferences_str = ", ".join([f"{k}: {v}" for k, v in user_data.get('learning_preferences', {}).items()])
                
                initial_system_prompt = f"""
                You are an AI tutor specializing in {st.session_state.current_study_subject}.
                Your responses should be tailored to the student's preferences and selected subject.
                Student's Grade Level: {student_grade}
                
                **IMPORTANT INSTRUCTION FOR EQUATIONS:**
                Whenever you present a chemical equation, mathematical formula, or any scientific notation, please format it using LaTeX.
                Use `$$...$$` for block equations (on their own line) and `$...$` for inline equations within text.
                For chemical symbols within LaTeX, use `\text{{Symbol}}` to ensure they are rendered as plain text (e.g., `$\text{{H}}_2\text{{O}}$` for H2O).
                Example: The balanced equation for water formation is $$\text{{2H}}_2 + \text{{O}}_2 \rightarrow \text{{2H}}_2\text{{O}}$$
                ---
                Syllabus for {st.session_state.current_study_subject}:
                {st.session_state.active_syllabus}
                ---
                Additional Context for {st.session_state.current_study_subject}:
                {st.session_state.active_subject_context}
                ---
                Be helpful, patient, and provide clear explanations. Ensure your answers are strictly within the scope of the provided syllabus and context.
                """

                # Add the system prompt as the very first message
                st.session_state.chat_history.append({"role": "system", "content": initial_system_prompt})

                # Add an initial message from the tutor to start the conversation
                initial_tutor_message = f"Hello! Welcome to your {st.session_state.current_study_subject} study session. I'm ready to help you with any questions you have based on the syllabus and context provided. How can I assist you today?"
                st.session_state.chat_history.append({"role": "assistant", "content": initial_tutor_message})
                save_chat_history() # Save initial messages to Firestore
                print(f"DEBUG: Initial chat history and system prompt set. Rerunning.") # Debug print
                st.rerun() # Rerun to display chat interface
            # No else for start_session_button here, as the outer 'if' handles the display flow
    else: # Subject is selected and context loaded, so show the chat interface
        print(f"DEBUG: Subject already selected ({st.session_state.current_study_subject}). Displaying chat interface.") # Debug print
        # --- Display Current Study Subject and Option to Change ---
        st.info(f"You are currently studying: **{st.session_state.current_study_subject}**")
        if st.button("Change Study Subject"):
            print("DEBUG: Change Study Subject button clicked. Resetting state.") # Debug print
            st.session_state.current_study_subject = None # Reset to prompt for new selection
            st.session_state.subject_context_loaded = False
            st.session_state.chat_history = [] # Clear history when changing subject
            st.rerun()
            return # Return here to immediately show the subject selection form

        # The student_grade selectbox is now defined at the top of tutor_page
        # so it's always available.

        # --- Chat Interface ---
        col1, col2 = st.columns([1, 2]) # Input on left, output/history on right

        with col1:
            st.subheader("Your Input")
            user_input = st.text_area("Type your question here:", height=150, key="user_input_area")
            send_button = st.button("Send to Tutor")
            
            # New: Generate Visual Explanation button
            generate_visual_button = st.button("Generate Visual Explanation", disabled=st.session_state.generating_image) # Disable while generating

        with col2:
            st.subheader("Chat History")
            chat_display_area = st.container(height=400, border=True)

            # Iterate through chat history, skipping the initial system message for display
            for chat_message in st.session_state.chat_history:
                if chat_message["role"] == "user":
                    chat_display_area.markdown(f"**You:** {chat_message['content']}")
                elif chat_message["role"] == "assistant": # Only display assistant messages
                    # Streamlit's markdown parser will automatically render LaTeX within $$...$$ or $...$
                    chat_display_area.markdown(f"**Tutor:** {chat_message['content']}")
                elif chat_message["role"] == "image": # Display generated images
                    chat_display_area.image(chat_message['content'], caption="AI Generated Visual")
            
            # Scroll to bottom
            st.markdown("<script>window.scrollTo(0, document.body.scrollHeight);</script>", unsafe_allow_html=True)

        if send_button and user_input:
            if current_tokens <= 0:
                st.error("You have no tokens left! Please contact support for more.")
                return

            # Decrement tokens for text interaction
            user_data['tokens'] -= 1
            update_user_data(user_data) # Save updated tokens to Firestore

            # Add user message to history
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            save_chat_history() # Save history to Firestore

            # Construct AI prompt context for this turn (re-using the system message already in history)
            messages = st.session_state.chat_history

            try:
                with st.spinner("Tutor is thinking..."):
                    client = openai.OpenAI(api_key=openai_api_key)
                    # Using gpt-4.1-nano for text responses
                    response = client.chat.completions.create(
                        model="gpt-4.1-nano", 
                        messages=messages,
                        max_tokens=500, # Increased max_tokens to allow for longer responses
                        temperature=0.7,
                    )
                    tutor_response = response.choices[0].message.content
                
                # Add tutor response to history
                st.session_state.chat_history.append({"role": "assistant", "content": tutor_response})
                
                # New: Play AI response as speech
                audio_bytes = text_to_speech(tutor_response)
                if audio_bytes:
                    st.audio(audio_bytes, format='audio/mp3', start_time=0)

                save_chat_history() # Save updated history to Firestore
                st.rerun() # Rerun to update chat display and token count

            except openai.APIError as e:
                st.error(f"OpenAI API error: {e}")
                # Revert token decrement if API call fails
                user_data['tokens'] += 1
                update_user_data(user_data)
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
                # Revert token decrement if API call fails
                user_data['tokens'] += 1
                update_user_data(user_data)

        elif generate_visual_button:
            # Cost for image generation (e.g., 50 tokens per image)
            # Note: DALL-E 3 pricing is per image ($0.04 for 1024x1024 standard), not token-based.
            # The 'tokens' here are abstract points for the user's balance.
            IMAGE_GENERATION_COST = 50 
            if current_tokens < IMAGE_GENERATION_COST:
                st.error(f"You need at least {IMAGE_GENERATION_COST} tokens to generate a visual. You have {current_tokens} tokens.")
                return

            # Decrement tokens for image generation
            user_data['tokens'] -= IMAGE_GENERATION_COST
            update_user_data(user_data) # Save updated tokens to Firestore

            # Get the last assistant message as context for image generation
            last_tutor_message = ""
            for msg in reversed(st.session_state.chat_history):
                if msg["role"] == "assistant":
                    last_tutor_message = msg["content"]
                    break
            
            if not last_tutor_message:
                st.warning("No recent tutor message to generate a visual from. Please ask a question first.")
                return

            # Use GPT-4.1-nano to generate a concise image prompt from the tutor's last response
            image_prompt_generation_messages = [
                {"role": "system", "content": "You are an assistant that generates concise, descriptive image prompts based on provided text, suitable for a visual learner. Focus on key concepts. Max 50 words."},
                {"role": "user", "content": f"Generate an image prompt based on this: {last_tutor_message}"}
            ]
            
            image_gen_prompt = ""
            try:
                with st.spinner("Crafting image prompt..."):
                    client = openai.OpenAI(api_key=openai_api_key)
                    prompt_response = client.chat.completions.create(
                        model="gpt-4.1-nano", # Using gpt-4.1-nano for prompt generation
                        messages=image_prompt_generation_messages,
                        max_tokens=50,
                        temperature=0.7
                    )
                    image_gen_prompt = prompt_response.choices[0].message.content
            except openai.APIError as e:
                st.error(f"Error generating image prompt: {e}")
                user_data['tokens'] += IMAGE_GENERATION_COST # Revert tokens
                update_user_data(user_data)
                return
            except Exception as e:
                st.error(f"An unexpected error occurred while crafting image prompt: {e}")
                user_data['tokens'] += IMAGE_GENERATION_COST # Revert tokens
                update_user_data(user_data)
                return

            if image_gen_prompt:
                # Add a temporary message to the chat history indicating image generation is starting
                st.session_state.chat_history.append({"role": "assistant", "content": f"Generating a visual for: '{image_gen_prompt}'"})
                save_chat_history()
                st.rerun() # Rerun to show the "Generating visual" message

                # Call the DALL-E API via the generate_image function
                generated_image_url = generate_image(image_gen_prompt) 

                if generated_image_url:
                    st.session_state.chat_history.append({"role": "image", "content": generated_image_url})
                    save_chat_history()
                    st.rerun() # Rerun to display the image
                else:
                    st.error("Failed to generate visual explanation.")
            else:
                st.warning("Could not generate a suitable image prompt.")
            
        st.markdown("---")
        if st.button("Back to Profile"):
            st.session_state.current_page = 'profile'
            st.rerun()

# --- Main App Logic ---
def main():
    """Controls the flow of the Streamlit application."""
    st.sidebar.title("Navigation")
    if st.session_state.logged_in:
        if st.sidebar.button("Profile"):
            st.session_state.current_page = 'profile'
            st.rerun()
        if st.sidebar.button("Tutor"):
            st.session_state.current_page = 'tutor'
            st.rerun()
        if st.sidebar.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.username = None
            st.session_state.user_data = None
            st.session_state.chat_history = []
            st.session_state.current_page = 'login'
            st.rerun()
    else:
        if st.sidebar.button("Login"):
            st.session_state.current_page = 'login'
            st.rerun()
        if st.sidebar.button("Register"):
            st.session_state.current_page = 'register'
            st.rerun()

    if st.session_state.current_page == 'login':
        login_page()
    elif st.session_state.current_page == 'register':
        register_page()
    elif st.session_state.current_page == 'profile':
        profile_page()
    elif st.session_state.current_page == 'tutor':
        tutor_page()

if __name__ == "__main__":
    main()
