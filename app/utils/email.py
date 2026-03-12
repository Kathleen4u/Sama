import os
import resend
from dotenv import load_dotenv
from app.routes.main import inject_now

load_dotenv()

resend.api_key = os.environ.get("RESEND_API_KEY")

def send_verification_email(user_email, user_name, token):
    # Generate verification URL
    verification_url = f"{os.environ.get('SITE_URL')}/verify?token={token}"
    current_year = inject_now()

    params = {
        "from": os.environ.get("FROM_EMAIL"),
        "to": [user_email],
        "subject": "Verify your email address",
        "html": f"""
        <!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify Your Email</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f5f5f5; padding: 40px 0;">
        <tr>
            <td align="center">
                <!-- Main Container -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
                    
                    <!-- Logo Section - FIXED -->
                    <tr>
                        <td align="center" style="padding: 40px 20px 30px 20px;">
                            <img src="https://stocksco-coral.vercel.app/static/images/StocksCo-logo.png" 
                                 alt="StocksCo Logo" 
                                 style="max-width: 120px; height: auto; display: block;">
                        </td>
                    </tr>
                    
                    <!-- Content Section -->
                    <tr>
                        <td style="padding: 0 60px 40px 60px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <h2 style="margin: 0; font-size: 20px; font-weight: 600; color: #333333; line-height: 1.4;">
                                            Hello {user_name},
                                        </h2>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <p style="margin: 0; font-size: 15px; color: #666666; line-height: 1.6;">
                                            Thanks for signing up with StocksCo! Before you get started trading with StocksCo, we need you to <span style="background-color: #FFF4E6; padding: 2px 4px; border-radius: 3px; color: #333;">confirm</span> your <span style="background-color: #FFF4E6; padding: 2px 4px; border-radius: 3px; color: #333;">email</span> address. Please click the button below to complete your signup.
                                        </p>
                                    </td>
                                </tr>
                                
                                <!-- Button -->
                                <tr>
                                    <td align="center" style="padding-bottom: 30px;">
                                        <a href="{verification_url}" style="display: inline-block; background-color: #1A73E8; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-size: 15px; font-weight: 600; box-shadow: 0 2px 4px rgba(26, 115, 232, 0.3);">
                                            Confirm Email Address
                                        </a>
                                    </td>
                                </tr>
                                
                                <!-- Alternative Link Section -->
                                <tr>
                                    <td style="padding-top: 20px; border-top: 1px solid #eeeeee;">
                                        <p style="margin: 0 0 10px 0; font-size: 13px; color: #666666; line-height: 1.5;">
                                            If you have any trouble clicking the button above, please copy and paste the URL below into your web browser.
                                        </p>
                                        <p style="margin: 0; font-size: 12px; color: #0066cc; word-break: break-all; line-height: 1.5;">
                                            <a href="{verification_url}" style="color: #0066cc; text-decoration: underline;">
                                                {verification_url}
                                            </a>
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td align="center" style="padding: 30px 20px; background-color: #fafafa; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;">
                            <p style="margin: 0 0 5px 0; font-size: 12px; color: #999999;">
                                © StocksCo Inc {current_year["year"]}
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #999999;">
                                Modern Trading for Everyone.
                            </p>
                        </td>
                    </tr>
                </table>
                
                <!-- Security Notice -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin-top: 20px;">
                    <tr>
                        <td align="center" style="padding: 0 20px;">
                            <p style="margin: 0; font-size: 11px; color: #999999; line-height: 1.5;">
                                This link will expire in 24 hours. If you didn't create an account, you can safely ignore this email.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """
    }

    try:
        email = resend.Emails.send(params)
        return email
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        raise

def send_reset_password_email(user_email, user_name, token):
    reset_url = f"{os.environ.get('SITE_URL')}/reset-password?token={token}"
    current_year = inject_now()

    params = {
        "from": os.environ.get("FROM_EMAIL"),
        "to": [user_email],
        "subject": "Reset Your Password",
        "html": f"""
            <!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reset Your Password</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f5f5f5; padding: 40px 0;">
        <tr>
            <td align="center">
                <!-- Main Container -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
                    
                    <!-- Logo Section -->
                    <tr>
                        <td align="center" style="padding: 40px 20px 30px 20px;">
                            <img src="https://stocksco-coral.vercel.app/static/images/StocksCo-logo.png" 
                                 alt="StocksCo Logo" 
                                 style="max-width: 120px; height: auto; display: block;">
                        </td>
                    </tr>
                    
                    <!-- Content Section -->
                    <tr>
                        <td style="padding: 0 60px 40px 60px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <h2 style="margin: 0; font-size: 20px; font-weight: 600; color: #333333; line-height: 1.4;">
                                            Hello {user_name},
                                        </h2>
                                    </td>
                                </tr>
                                
                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <p style="margin: 0; font-size: 15px; color: #666666; line-height: 1.6;">
                                            We received a request to reset the password for your StocksCo account. Click the button below to <span style="background-color: #FFF4E6; padding: 2px 4px; border-radius: 3px; color: #333;">reset</span> your <span style="background-color: #FFF4E6; padding: 2px 4px; border-radius: 3px; color: #333;">password</span> and regain access to your account.
                                        </p>
                                    </td>
                                </tr>
                                
                                <!-- Button -->
                                <tr>
                                    <td align="center" style="padding-bottom: 30px;">
                                        <a href="{reset_url}" style="display: inline-block; background-color: #1A73E8; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-size: 15px; font-weight: 600; box-shadow: 0 2px 4px rgba(26, 115, 232, 0.3);">
                                            Reset Password
                                        </a>
                                    </td>
                                </tr>
                                
                                <!-- Alternative Link Section -->
                                <tr>
                                    <td style="padding-top: 20px; border-top: 1px solid #eeeeee;">
                                        <p style="margin: 0 0 10px 0; font-size: 13px; color: #666666; line-height: 1.5;">
                                            If you have any trouble clicking the button above, please copy and paste the URL below into your web browser.
                                        </p>
                                        <p style="margin: 0; font-size: 12px; color: #0066cc; word-break: break-all; line-height: 1.5;">
                                            <a href="{reset_url}" style="color: #0066cc; text-decoration: underline;">
                                                {reset_url}
                                            </a>
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td align="center" style="padding: 30px 20px; background-color: #fafafa; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;">
                            <p style="margin: 0 0 5px 0; font-size: 12px; color: #999999;">
                                © StocksCo Inc {current_year["year"]}
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #999999;">
                                Modern Trading for Everyone.
                            </p>
                        </td>
                    </tr>
                </table>
                
                <!-- Security Notice -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin-top: 20px;">
                    <tr>
                        <td align="center" style="padding: 0 20px;">
                            <p style="margin: 0; font-size: 11px; color: #999999; line-height: 1.5;">
                                This link will expire in 1 hour. If you didn't request a password reset, please ignore this email or contact support if you have concerns.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
            """
    }

    try:
        email = resend.Emails.send(params)
        return email
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        raise


def send_payment_confirmation_email(user_email, user_name, amount):
    current_year = inject_now()

    params = {
        "from": os.environ.get("FROM_EMAIL"),
        "to": [user_email],
        "subject": "Payment Confirmation",
        "html": f"""
            <!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Confirmation</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f5f5f5; padding: 40px 0;">
        <tr>
            <td align="center">
                <!-- Main Container -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">

                    <!-- Logo Section -->
                    <tr>
                        <td align="center" style="padding: 40px 20px 30px 20px;">
                            <img src="https://stocksco-coral.vercel.app/static/images/StocksCo-logo.png" 
                                 alt="StocksCo Logo" 
                                 style="max-width: 120px; height: auto; display: block;">
                        </td>
                    </tr>

                    <!-- Content Section -->
                    <tr>
                        <td style="padding: 0 60px 40px 60px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <h2 style="margin: 0; font-size: 20px; font-weight: 600; color: #333333; line-height: 1.4;">
                                            Hello {user_name},
                                        </h2>
                                    </td>
                                </tr>

                                <tr>
                                    <td style="padding-bottom: 30px;">
                                        <p style="margin: 0; font-size: 15px; color: #666666; line-height: 1.6;">
                                            You have successfully funded your StocksCo account with ${amount}.
                                        </p>
                                    </td>
                                </tr>      
                            </table>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td align="center" style="padding: 30px 20px; background-color: #fafafa; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;">
                            <p style="margin: 0 0 5px 0; font-size: 12px; color: #999999;">
                                © StocksCo Inc {current_year["year"]}
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #999999;">
                                Modern Trading for Everyone.
                            </p>
                        </td>
                    </tr>
                </table>

                <!-- Security Notice -->
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin-top: 20px;">
                    <tr>
                        <td align="center" style="padding: 0 20px;">
                            <p style="margin: 0; font-size: 11px; color: #999999; line-height: 1.5;">
                                This link will expire in 1 hour. If you didn't request a password reset, please ignore this email or contact support if you have concerns.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
            """
    }

    try:
        email = resend.Emails.send(params)
        return email
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        raise