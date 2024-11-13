from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room, emit
from pymongo import MongoClient, DESCENDING
from bson import ObjectId
import os
from dataclasses import dataclass
from typing import Dict, Set
import asyncio
from werkzeug.utils import secure_filename
import gridfs
from datetime import datetime
from flask import send_file
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# MongoDB Connection
MONGO_URI = os.getenv('MONGO_URI', 'mongodb+srv://rauanbratan:zbLsoOm07qbtp2YQ@teack-talk.c9jxv.mongodb.net/?retryWrites=true&w=majority&appName=teack-talk')
client = MongoClient(MONGO_URI)
db = client['teachtalk']  # database name
users_collection = db['users']
messages_collection = db['messages']
chat_rooms_collection = db['chat_rooms']
tasks_collection = db['tasks']


# Initialize GridFS for file storage
fs = gridfs.GridFS(db)

# File upload configurations
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def serialize_document(document):
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(value, ObjectId):
                document[key] = str(value)
            elif isinstance(value, datetime):
                document[key] = value.isoformat()
            elif isinstance(value, list):
                document[key] = [serialize_document(item) if isinstance(item, dict) else str(item) if isinstance(item, ObjectId) else item for item in value]
        return document
    return document


# User registration endpoint
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')

    if not username or not password or not email:
        return jsonify({'message': 'Username, email, and password are required'}), 400

    if users_collection.find_one({'username': username}):
        return jsonify({'message': 'User already exists'}), 400

    new_user = {'username': username, 'password': password, 'email': email}
    users_collection.insert_one(new_user)
    return jsonify({'message': 'User registered successfully', 'user': {'username': username, 'email': email}}), 201

# User login endpoint
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'message': 'Email and password are required'}), 400

    user = users_collection.find_one({'email': email, 'password': password})
    if user:
        return jsonify({'message': 'Login successful', 'user': {'username': user['username'], 'email': email}}), 200
    return jsonify({'message': 'Invalid email or password'}), 401

# Fetch user list endpoint
@app.route('/users', methods=['GET'])
def get_users():
    users = users_collection.find()
    user_list = [{'username': user['username'], 'email': user['email']} for user in users]
    return jsonify(user_list), 200

# Chat room endpoints
@app.route('/chat_rooms', methods=['GET'])
def get_chat_rooms():
    chat_rooms = chat_rooms_collection.find()
    rooms = [{'id': str(room['_id']), 'name': room['name'], 'lastMessage': room.get('lastMessage', 'No messages yet.')} for room in chat_rooms]
    return jsonify(rooms), 200

@app.route('/chat_rooms', methods=['POST'])
def create_chat_room():
    data = request.json
    room_name = data.get('name')
    if not room_name:
        return jsonify({'message': 'Chat room name is required'}), 400

    new_room = {'name': room_name, 'lastMessage': 'No messages yet.'}
    result = chat_rooms_collection.insert_one(new_room)
    new_room['_id'] = str(result.inserted_id)
    return jsonify({'message': 'Chat room created successfully', 'room': new_room}), 201

# Task endpoints
@app.route('/tasks', methods=['GET'])
def get_tasks():
    try:
        tasks = tasks_collection.find().sort('created_at', DESCENDING)
        task_list = []
        
        for task in tasks:
            # Get documents for this task
            documents = []
            if 'documents' in task and task['documents']:
                for doc_id in task['documents']:
                    try:
                        file_info = fs.get(doc_id)
                        if file_info:
                            # Safely get metadata
                            metadata = getattr(file_info, 'metadata', {}) or {}
                            documents.append({
                                '_id': str(doc_id),
                                'filename': getattr(file_info, 'filename', 'unknown'),
                                'upload_date': file_info.upload_date.isoformat() if getattr(file_info, 'upload_date', None) else None,
                                'content_type': metadata.get('content_type', 'application/octet-stream')
                            })
                    except (gridfs.errors.NoFile, Exception) as e:
                        print(f"Error accessing file {doc_id}: {str(e)}")
                        continue
            
            # Safely handle datetime
            created_at = task.get('created_at', datetime.utcnow())
            if isinstance(created_at, datetime):
                created_at = created_at.isoformat()
            
            task_data = {
                'id': str(task['_id']),
                'title': task.get('title', ''),
                'description': task.get('description', ''),
                'completed': task.get('completed', False),
                'created_at': created_at,
                'documents': documents
            }
            task_list.append(task_data)
        
        return jsonify(task_list), 200
    
    except Exception as e:
        print(f"Error fetching tasks: {str(e)}")
        return jsonify({'error': 'Failed to fetch tasks'}), 500

@app.route('/tasks', methods=['POST'])
def create_task():
    try:
        title = request.form.get('title')
        description = request.form.get('description', '')
        
        if not title:
            return jsonify({'error': 'Title is required'}), 400

        # Create task document
        current_time = datetime.utcnow()
        new_task = {
            'title': title,
            'description': description,
            'completed': False,
            'created_at': current_time,
            'updated_at': current_time,
            'documents': []
        }

        # Handle file uploads
        if 'documents' in request.files:
            files = request.files.getlist('documents')
            for file in files:
                if file and allowed_file(file.filename):
                    try:
                        filename = secure_filename(file.filename)
                        # Add metadata when saving to GridFS
                        metadata = {
                            'content_type': file.content_type or 'application/octet-stream',
                            'upload_date': current_time,
                            'original_filename': file.filename
                        }
                        
                        file_id = fs.put(
                            file,
                            filename=filename,
                            metadata=metadata
                        )
                        new_task['documents'].append(file_id)
                    except Exception as e:
                        print(f"Error uploading file {file.filename}: {str(e)}")
                        continue

        # Insert task into database
        result = tasks_collection.insert_one(new_task)
        
        # Prepare response
        response_task = {
            'id': str(result.inserted_id),
            'title': new_task['title'],
            'description': new_task['description'],
            'completed': new_task['completed'],
            'created_at': new_task['created_at'].isoformat(),
            'documents': []
        }

        # Add document info to response
        for doc_id in new_task['documents']:
            try:
                file_info = fs.get(doc_id)
                metadata = getattr(file_info, 'metadata', {}) or {}
                response_task['documents'].append({
                    '_id': str(doc_id),
                    'filename': file_info.filename,
                    'upload_date': file_info.upload_date.isoformat() if getattr(file_info, 'upload_date', None) else None,
                    'content_type': metadata.get('content_type', 'application/octet-stream')
                })
            except Exception as e:
                print(f"Error getting file info for {doc_id}: {str(e)}")
                continue

        return jsonify(response_task), 201
    
    except Exception as e:
        print(f"Error creating task: {str(e)}")
        return jsonify({'error': 'Failed to create task'}), 500

@app.route('/tasks/<task_id>', methods=['PUT'])
def edit_task(task_id):
    try:
        title = request.form.get('title')
        description = request.form.get('description')
        completed = request.form.get('completed', 'false').lower() == 'true'

        if not title:
            return jsonify({'error': 'Title is required'}), 400

        # Find existing task
        task = tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({'error': 'Task not found'}), 404

        # Prepare update data
        update_data = {
            'title': title,
            'description': description,
            'completed': completed,
            'updated_at': datetime.utcnow()
        }

        # Handle file uploads
        if 'documents' in request.files:
            files = request.files.getlist('documents')
            for file in files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file_id = fs.put(
                        file,
                        filename=filename,
                        content_type=file.content_type,
                        upload_date=datetime.utcnow()
                    )
                    if 'documents' not in update_data:
                        update_data['documents'] = task.get('documents', [])
                    update_data['documents'].append(file_id)

        # Update task
        result = tasks_collection.update_one(
            {'_id': ObjectId(task_id)},
            {'$set': update_data}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Task not found or no changes made'}), 404

        # Get updated task
        updated_task = tasks_collection.find_one({'_id': ObjectId(task_id)})
        response_task = serialize_document(updated_task)
        response_task['id'] = str(response_task.pop('_id'))

        return jsonify(response_task), 200

    except Exception as e:
        print(f"Error updating task: {str(e)}")
        return jsonify({'error': 'Failed to update task'}), 500

@app.route('/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    try:
        # Find task to get document IDs
        task = tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({'error': 'Task not found'}), 404

        # Delete associated documents from GridFS
        if 'documents' in task:
            for doc_id in task['documents']:
                try:
                    fs.delete(doc_id)
                except gridfs.errors.NoFile:
                    continue

        # Delete task
        result = tasks_collection.delete_one({'_id': ObjectId(task_id)})
        if result.deleted_count == 0:
            return jsonify({'error': 'Task not found'}), 404

        return jsonify({'message': 'Task and associated documents deleted successfully'}), 200

    except Exception as e:
        print(f"Error deleting task: {str(e)}")
        return jsonify({'error': 'Failed to delete task'}), 500

@app.route('/tasks/<task_id>/documents/<document_id>', methods=['GET'])
def download_document(task_id, document_id):
    try:
        # Verify task exists
        task = tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({'error': 'Task not found'}), 404

        # Get file from GridFS
        file_id = ObjectId(document_id)
        if not task.get('documents') or file_id not in task['documents']:
            return jsonify({'error': 'Document not found'}), 404

        file_data = fs.get(file_id)
        if not file_data:
            return jsonify({'error': 'File not found in GridFS'}), 404

        # Safely get content type from metadata
        metadata = getattr(file_data, 'metadata', {}) or {}
        content_type = metadata.get('content_type', 'application/octet-stream')
        
        return send_file(
            file_data,
            download_name=getattr(file_data, 'filename', 'download'),
            mimetype=content_type,
            as_attachment=True
        )
    
    except Exception as e:
        print(f"Error downloading document: {str(e)}")
        return jsonify({'error': 'Failed to download document'}), 500

# Helper function to safely get nested dictionary values
def safe_get(obj, *keys, default=None):
    try:
        for key in keys:
            if obj is None:
                return default
            obj = obj.get(key, default)
        return obj
    except Exception:
        return default
    
    except Exception as e:
        print(f"Error downloading document: {str(e)}")
        return jsonify({'error': 'Failed to download document'}), 500

@app.route('/tasks/<task_id>/documents/<document_id>', methods=['DELETE'])
def delete_document(task_id, document_id):
    try:
        task_id_obj = ObjectId(task_id)
        document_id_obj = ObjectId(document_id)

        # Update task to remove document reference
        result = tasks_collection.update_one(
            {'_id': task_id_obj},
            {
                '$pull': {'documents': document_id_obj},
                '$set': {'updated_at': datetime.utcnow()}
            }
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Document not found'}), 404

        # Delete file from GridFS
        try:
            fs.delete(document_id_obj)
        except gridfs.errors.NoFile:
            pass  # File already deleted

        return jsonify({'message': 'Document deleted successfully'}), 200
    
    except Exception as e:
        print(f"Error deleting document: {str(e)}")
        return jsonify({'error': 'Failed to delete document'}), 500

# WebSocket Events for chat and WebRTC signaling
@socketio.on('join_room')
def handle_join_room(data):
    room = data.get('room')
    username = data.get('username')
    
    # Join the room
    join_room(room)

    # Emit a message that the user has joined the room
    emit('receive_message', {'message': f"{username} has joined the room."}, room=room)

    # Fetch and emit previous messages for the room to the newly joined user
    previous_messages = messages_collection.find({'room': room})
    previous_messages_list = [{'username': msg['username'], 'message': msg['message'], 'timestamp': msg['timestamp']} for msg in previous_messages]
    
    # Emit the previous messages only to the user who joined
    emit('load_previous_messages', previous_messages_list, room=request.sid)

@socketio.on('leave_room')
def handle_leave_room(data):
    room = data.get('room')
    username = data.get('username')
    leave_room(room)
    emit('receive_message', {'message': f"{username} has left the room."}, room=room)

@socketio.on('send_message')
def handle_send_message(data):
    room = data.get('room')
    username = data.get('username')
    message = data.get('message')
    timestamp = data.get('timestamp')

    messages_collection.insert_one({'room': room, 'username': username, 'message': message, 'timestamp': timestamp})
    chat_rooms_collection.update_one({'name': room}, {'$set': {'lastMessage': message}})

    emit('receive_message', {'username': username, 'message': message, 'timestamp': timestamp}, room=room)

# WebRTC signaling events for calls
@dataclass
class CallSession:
    caller: str
    callee: str
    room_id: str
    status: str = 'pending'

# Add this to your existing Flask app
active_calls: Dict[str, CallSession] = {}
user_socket_map: Dict[str, str] = {}

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('register_user')
def handle_register_user(data):
    username = data.get('username')
    if username:
        user_socket_map[username] = request.sid
        print(f"Registered user {username} with socket {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    # Remove user from socket map
    username = next((user for user, sid in user_socket_map.items() if sid == request.sid), None)
    if username:
        del user_socket_map[username]
        
        # End any active calls involving this user
        for room_id, session in active_calls.items():
            if username in (session.caller, session.callee):
                emit('call_ended', room=room_id)
                del active_calls[room_id]

@socketio.on('initiate_call')
def handle_initiate_call(data):
    caller = data.get('caller')
    callee = data.get('callee')
    call_type = data.get('type', 'video')
    
    if not all([caller, callee]):
        return {'error': 'Missing caller or callee information'}
    
    if callee not in user_socket_map:
        return {'error': 'Callee is not online'}
    
    room_id = f"call_{caller}_{callee}"
    
    # Check if either user is already in a call
    for session in active_calls.values():
        if caller in (session.caller, session.callee) or callee in (session.caller, session.callee):
            return {'error': 'One of the users is already in a call'}
    
    # Create new call session
    active_calls[room_id] = CallSession(caller=caller, callee=callee, room_id=room_id)
    
    # Join the room
    join_room(room_id)
    
    # Notify callee
    callee_sid = user_socket_map.get(callee)
    if callee_sid:
        emit('incoming_call', {
            'caller': caller,
            'type': call_type,
            'room': room_id
        }, room=callee_sid)
    
    return {'success': True, 'room': room_id}

@socketio.on('answer_call')
def handle_answer_call(data):
    room = data.get('room')
    answer = data.get('answer')
    
    if room not in active_calls:
        return {'error': 'Call session not found'}
    
    session = active_calls[room]
    session.status = 'active' if answer else 'rejected'
    
    if answer:
        join_room(room)
        emit('call_accepted', {'room': room}, room=room)
    else:
        emit('call_rejected', {'room': room}, room=room)
        del active_calls[room]

@socketio.on('webrtc_signal')
def handle_webrtc_signal(data):
    room = data.get('room')
    signal = data.get('signal')
    
    if room not in active_calls:
        return {'error': 'Call session not found'}
    
    # Broadcast the signal to all peers in the room except sender
    emit('webrtc_signal', {'signal': signal}, room=room, skip_sid=request.sid)

@socketio.on('end_call')
def handle_end_call(data):
    room = data.get('room')
    
    if room not in active_calls:
        return {'error': 'Call session not found'}
    
    # Notify all participants
    emit('call_ended', room=room)
    
    # Clean up
    del active_calls[room]

if __name__ == '__main__':
    socketio.run(app, debug=True)
