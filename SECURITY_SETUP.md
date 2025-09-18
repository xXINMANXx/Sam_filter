# Sam Filter Application - Security Setup Guide

## 🚨 CRITICAL SECURITY NOTICE

This guide contains essential security configuration steps that **MUST** be completed before deploying the Sam Filter application to production.

## Required Environment Variables

### 1. SECRET_KEY (CRITICAL)
**Purpose:** Used for session encryption and security token generation
**Security Level:** CRITICAL - Application security depends on this

```bash
# Generate a secure secret key (Windows PowerShell)
$secretKey = -join ((33..126) | Get-Random -Count 64 | % {[char]$_})
$env:SECRET_KEY = $secretKey

# Alternative: Use Python to generate
python -c "import secrets; print(f'SECRET_KEY={secrets.token_hex(32)}')"
```

**Production Setup:**
- Set in your environment/deployment configuration
- **NEVER** commit to version control
- Rotate periodically (every 90 days recommended)

### 2. OPENAI_API_KEY (CRITICAL)
**Purpose:** Required for AI summary generation features
**Security Level:** CRITICAL - Financial implications if leaked

```bash
# Set your OpenAI API key
$env:OPENAI_API_KEY = "sk-your-actual-api-key-here"
```

**Security Requirements:**
- Obtain from https://platform.openai.com/api-keys
- Store securely in environment variables
- Monitor usage and set billing limits
- **The old hardcoded key in the source code has been removed**

### 3. PRODUCTION_MODE (Important)
**Purpose:** Disables diagnostic endpoints and enables security features
**Security Level:** Important for production deployment

```bash
# Enable production mode
$env:PRODUCTION_MODE = "true"
```

**Effects when enabled:**
- Disables `/diag/*` endpoints
- Disables `/reload-info` endpoint
- Enhanced security logging
- Stricter error handling

## Windows Environment Variable Setup

### Method 1: PowerShell (Current Session)
```powershell
# Set for current PowerShell session
$env:SECRET_KEY = "your-64-character-secret-key-here"
$env:OPENAI_API_KEY = "sk-your-openai-key-here"
$env:PRODUCTION_MODE = "false"  # Set to "true" for production
```

### Method 2: System Environment Variables (Persistent)
1. Press `Win + X` → Select "System"
2. Click "Advanced system settings"
3. Click "Environment Variables..."
4. Under "User variables" or "System variables", click "New..."
5. Add each variable:
   - `SECRET_KEY` = your generated secret key
   - `OPENAI_API_KEY` = your OpenAI API key
   - `PRODUCTION_MODE` = "true" (for production)

### Method 3: .env File (Development Only)
Create a `.env` file in the application directory:
```
SECRET_KEY=your-64-character-secret-key-here
OPENAI_API_KEY=sk-your-openai-key-here
PRODUCTION_MODE=false
```

**⚠️ WARNING:** Add `.env` to `.gitignore` to prevent committing secrets!

## Security Verification Checklist

### Before Starting the Application:
- [ ] `SECRET_KEY` environment variable is set with 32+ character random value
- [ ] `OPENAI_API_KEY` environment variable is set with valid API key
- [ ] `PRODUCTION_MODE=true` is set for production deployments
- [ ] Hard-coded secrets have been removed from source code
- [ ] OpenAI API key has usage limits configured
- [ ] Application logs do not contain sensitive information

### After Starting the Application:
- [ ] Check startup logs for security warnings
- [ ] Verify diagnostic endpoints return 404 in production mode
- [ ] Test file upload restrictions are working
- [ ] Confirm security headers are present in HTTP responses
- [ ] Validate session cookies have proper security flags

## Security Features Implemented

### ✅ Fixed Critical Issues:
1. **API Key Security**: Removed hardcoded OpenAI API key
2. **Session Security**: Implemented strong random session keys
3. **Path Traversal**: Fixed file serving vulnerability with `safe_join`
4. **Browser Security**: Secured Selenium configuration
5. **Input Validation**: Added validation to critical endpoints
6. **Security Headers**: Implemented comprehensive HTTP security headers
7. **Production Hardening**: Disabled diagnostic endpoints in production

### 🔧 Additional Recommendations:

#### For Production Deployment:
1. **Enable HTTPS**: Set `SESSION_COOKIE_SECURE=True` when using HTTPS
2. **Reverse Proxy**: Deploy behind nginx/Apache with security configuration
3. **Rate Limiting**: Implement request rate limiting
4. **Monitoring**: Set up security event logging and monitoring
5. **Updates**: Regularly update dependencies for security patches

#### For Enhanced Security:
1. **Authentication**: Implement user authentication for sensitive operations
2. **Authorization**: Add role-based access control
3. **CSRF Protection**: Consider adding Flask-WTF for CSRF tokens
4. **Database Security**: If adding database, use parameterized queries
5. **File Scanning**: Consider virus scanning for uploaded files

## Compliance Considerations

For government contracting applications, consider:

- **FISMA Compliance**: Federal Information Security Management Act
- **NIST Cybersecurity Framework**: Alignment with federal guidelines
- **Data Retention**: Government record-keeping requirements
- **Access Controls**: Audit trails and user access management
- **Encryption**: Data encryption at rest and in transit

## Emergency Response

### If API Key is Compromised:
1. **Immediately revoke** the compromised key at OpenAI platform
2. **Generate new key** and update environment variables
3. **Monitor usage** for unauthorized API calls
4. **Review logs** for potential data exposure
5. **Update billing alerts** to detect unusual usage

### If Session Key is Compromised:
1. **Generate new SECRET_KEY** immediately
2. **Restart application** to invalidate all sessions
3. **Force all users to re-authenticate**
4. **Review access logs** for suspicious activity

## Support and Updates

- Keep dependencies updated with `pip install -r requirements.txt --upgrade`
- Monitor security advisories for Flask, Selenium, and other dependencies
- Review this security configuration quarterly
- Test security measures regularly

---

**🔐 Remember: Security is an ongoing process, not a one-time setup!**

For questions about security configuration, review the application logs and consult the Flask security documentation.