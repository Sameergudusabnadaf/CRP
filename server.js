const express = require('express');
const fs = require('fs');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Enable CORS for local file testing
app.use((req, res, next) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, PATCH, DELETE');
    res.setHeader('Access-Control-Allow-Headers', 'X-Requested-With,content-type');
    res.setHeader('Access-Control-Allow-Credentials', true);
    next();
});

// Serve static files from the current directory
app.use(express.static(__dirname));

const SETTINGS_FILE = path.join(__dirname, 'settings.json');

const defaultUrls = {
    kannada: 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing',
    english: 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing',
    maths: 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing'
};

// GET settings
app.get('/api/settings', (req, res) => {
    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        if (err) {
            // If file does not exist, return defaults
            if (err.code === 'ENOENT') {
                return res.json(defaultUrls);
            }
            return res.status(500).json({ error: 'Failed to read settings' });
        }
        try {
            const settings = JSON.parse(data);
            res.json({ ...defaultUrls, ...settings });
        } catch (e) {
            res.json(defaultUrls);
        }
    });
});

// POST settings
app.post('/api/settings', (req, res) => {
    const newSettings = req.body;
    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        let settings = {};
        if (!err) {
            try {
                settings = JSON.parse(data);
            } catch (e) {}
        }
        
        if (!settings.submissions) settings.submissions = {};
        
        const hasSubjectChanged = (oldList, newList) => {
            if (!oldList && !newList) return false;
            if (!oldList || !newList) return true;
            if (oldList.length !== newList.length) return true;
            for (let i = 0; i < oldList.length; i++) {
                if (oldList[i].name !== newList[i].name || oldList[i].url !== newList[i].url) {
                    return true;
                }
            }
            return false;
        };

        let clearedSubmissions = 0;

        const clearSubmissionsForSubject = (subj) => {
            for (const key in settings.submissions) {
                if (settings.submissions[key].subject === subj) {
                    delete settings.submissions[key];
                    clearedSubmissions++;
                }
            }
        };

        if (newSettings.kannada !== undefined) {
            if (hasSubjectChanged(settings.kannada, newSettings.kannada)) {
                clearSubmissionsForSubject('kannada');
            }
            settings.kannada = newSettings.kannada;
        }
        if (newSettings.english !== undefined) {
            if (hasSubjectChanged(settings.english, newSettings.english)) {
                clearSubmissionsForSubject('english');
            }
            settings.english = newSettings.english;
        }
        if (newSettings.maths !== undefined) {
            if (hasSubjectChanged(settings.maths, newSettings.maths)) {
                clearSubmissionsForSubject('maths');
            }
            settings.maths = newSettings.maths;
        }

        // Adjust statistics based on cleared submissions
        if (clearedSubmissions > 0) {
            let completed = settings.completedCount !== undefined ? Number(settings.completedCount) : 35;
            let pending = settings.notCompletedCount !== undefined ? Number(settings.notCompletedCount) : 12;
            
            settings.completedCount = Math.max(0, completed - clearedSubmissions);
            settings.notCompletedCount = pending + clearedSubmissions;
        }
        
        fs.writeFile(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf8', (err) => {
            if (err) {
                return res.status(500).json({ error: 'Failed to save settings' });
            }
            res.json({ success: true, settings });
        });
    });
});

// Keep track of active user sessions
const activeSessions = new Map();

// POST heartbeat
app.post('/api/heartbeat', (req, res) => {
    const { userId, role } = req.body;
    if (userId) {
        activeSessions.set(`${role}:${userId}`, Date.now());
    }
    res.json({ success: true });
});

// GET stats
app.get('/api/stats', (req, res) => {
    const now = Date.now();
    // Clear sessions older than 30 seconds
    for (const [key, timestamp] of activeSessions.entries()) {
        if (now - timestamp > 30000) {
            activeSessions.delete(key);
        }
    }
    
    // Count active teacher sessions
    let activeTeacherCount = 0;
    for (const key of activeSessions.keys()) {
        if (key.startsWith('teacher')) {
            activeTeacherCount++;
        }
    }

    // Read settings.json to get any configured completed/pending counts
    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        let completed = 35;
        let notCompleted = 12;
        let submissionsList = [];
        if (!err) {
            try {
                const settings = JSON.parse(data);
                if (settings.completedCount !== undefined) completed = Number(settings.completedCount);
                if (settings.notCompletedCount !== undefined) notCompleted = Number(settings.notCompletedCount);
                if (settings.submissions) {
                    submissionsList = Object.values(settings.submissions);
                }
            } catch (e) {}
        }
        res.json({
            activeTeachers: 24 + activeTeacherCount,
            completedTasks: completed,
            notCompletedTasks: notCompleted,
            submissions: submissionsList,
            users: settings.users || []
        });
    });
});

// POST submit subject progress
app.post('/api/submit', (req, res) => {
    const { teacher, subject, sheetUrl } = req.body;
    if (!teacher || !subject) {
        return res.status(400).json({ error: 'Missing teacher or subject parameter' });
    }
    
    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        let settings = {};
        if (!err) {
            try {
                settings = JSON.parse(data);
            } catch (e) {}
        }
        
        if (!settings.submissions) {
            settings.submissions = {};
        }
        
        // Key submission on teacher name, subject, and specific sheet URL to allow multiple sheets
        const key = sheetUrl ? `${teacher}:${subject}:${sheetUrl}` : `${teacher}:${subject}`;
        if (!settings.submissions[key]) {
            // Save detailed submission metadata
            settings.submissions[key] = {
                teacher,
                subject,
                timestamp: new Date().toISOString(),
                sheetUrl: sheetUrl || ''
            };
            
            // Adjust statistics dynamically
            let completed = settings.completedCount !== undefined ? Number(settings.completedCount) : 35;
            let pending = settings.notCompletedCount !== undefined ? Number(settings.notCompletedCount) : 12;
            
            settings.completedCount = completed + 1;
            if (pending > 0) {
                settings.notCompletedCount = pending - 1;
            }
            
            fs.writeFile(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf8', (err) => {
                if (err) {
                    return res.status(500).json({ error: 'Failed to record submission' });
                }
                res.json({ success: true, completed: settings.completedCount, notCompleted: settings.notCompletedCount });
            });
        } else {
            res.json({ success: true, message: 'Already submitted' });
        }
    });
});

// POST delete submission
app.post('/api/delete-submission', (req, res) => {
    const { teacher, subject, sheetUrl } = req.body;
    if (!teacher || !subject) {
        return res.status(400).json({ error: 'Missing teacher or subject parameter' });
    }
    
    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        let settings = {};
        if (!err) {
            try {
                settings = JSON.parse(data);
            } catch (e) {}
        }
        
        if (settings.submissions) {
            let keyToDelete = null;
            for (const key in settings.submissions) {
                const sub = settings.submissions[key];
                if (sub.teacher === teacher && 
                    sub.subject === subject && 
                    (sub.sheetUrl || '') === (sheetUrl || '')) {
                    keyToDelete = key;
                    break;
                }
            }
            
            if (keyToDelete) {
                delete settings.submissions[keyToDelete];
                
                let completed = settings.completedCount !== undefined ? Number(settings.completedCount) : 35;
                let pending = settings.notCompletedCount !== undefined ? Number(settings.notCompletedCount) : 12;
                
                settings.completedCount = Math.max(0, completed - 1);
                settings.notCompletedCount = pending + 1;
                
                fs.writeFile(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf8', (err) => {
                    if (err) {
                        return res.status(500).json({ error: 'Failed to delete submission' });
                    }
                    res.json({ success: true, completed: settings.completedCount, notCompleted: settings.notCompletedCount });
                });
            } else {
                res.status(404).json({ error: 'Submission not found' });
            }
        } else {
            res.status(404).json({ error: 'Submissions not found' });
        }
    });
});

// POST add user
app.post('/api/add-user', (req, res) => {
    const { email, name, password } = req.body;
    if (!email || !name || !password) {
        return res.status(400).json({ error: 'Missing email, name, or password parameter' });
    }

    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        let settings = {};
        if (!err) {
            try {
                settings = JSON.parse(data);
            } catch (e) {}
        }

        if (!settings.users) {
            settings.users = [];
        }

        const exists = settings.users.some(u => u.email === email);
        if (!exists) {
            settings.users.push({ email, name, password });
            fs.writeFile(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf8', (err) => {
                if (err) {
                    return res.status(500).json({ error: 'Failed to add user' });
                }
                res.json({ success: true, users: settings.users });
            });
        } else {
            res.status(409).json({ error: 'User already exists' });
        }
    });
});

// POST delete user
app.post('/api/delete-user', (req, res) => {
    const { email } = req.body;
    if (!email) {
        return res.status(400).json({ error: 'Missing email parameter' });
    }

    fs.readFile(SETTINGS_FILE, 'utf8', (err, data) => {
        if (err) {
            return res.status(500).json({ error: 'Failed to read settings' });
        }
        let settings = {};
        try {
            settings = JSON.parse(data);
        } catch (e) {
            return res.status(500).json({ error: 'Failed to parse settings' });
        }

        if (settings.users) {
            const initialLen = settings.users.length;
            settings.users = settings.users.filter(u => u.email !== email);
            if (settings.users.length < initialLen) {
                fs.writeFile(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf8', (err) => {
                    if (err) {
                        return res.status(500).json({ error: 'Failed to save settings' });
                    }
                    return res.json({ success: true, users: settings.users });
                });
            } else {
                res.status(404).json({ error: 'User not found' });
            }
        } else {
            res.status(404).json({ error: 'Users list not found' });
        }
    });
});

// Fallback to index.html for root path
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`Server is running at http://localhost:${PORT}`);
    console.log(`Accessible on local network at http://YOUR_IP_ADDRESS:${PORT}`);
});
