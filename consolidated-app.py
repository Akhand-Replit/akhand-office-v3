

import streamlit as st
import datetime
import time
from datetime import timedelta
import io
from sqlalchemy import create_engine, text
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from io import BytesIO

#########################################
# DATABASE CONNECTION
#########################################

@st.cache_resource
def init_connection():
    """Initialize database connection with caching.
    
    Returns:
        SQLAlchemy engine or None if connection fails
    """
    try:
        return create_engine(st.secrets["postgres"]["url"])
    except Exception as e:
        st.error(f"Database connection error: {e}")
        return None

def init_db(engine):
    """Initialize database tables if they don't exist.
    
    Args:
        engine: SQLAlchemy database engine
    """
    with engine.connect() as conn:
        conn.execute(text('''
        -- Companies table
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(100) UNIQUE NOT NULL,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            profile_pic_url TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Branches table (with parent branch support)
        CREATE TABLE IF NOT EXISTS branches (
            id SERIAL PRIMARY KEY,
            company_id INTEGER REFERENCES companies(id),
            parent_branch_id INTEGER REFERENCES branches(id),
            branch_name VARCHAR(100) NOT NULL,
            is_main_branch BOOLEAN DEFAULT FALSE,
            location VARCHAR(255),
            branch_head VARCHAR(100),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, branch_name)
        );
        
        -- Employee Roles table
        CREATE TABLE IF NOT EXISTS employee_roles (
            id SERIAL PRIMARY KEY,
            role_name VARCHAR(50) NOT NULL,
            role_level INTEGER NOT NULL,
            company_id INTEGER REFERENCES companies(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, role_name)
        );
        
        -- Messages table
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender_type VARCHAR(20) NOT NULL, -- 'admin' or 'company'
            sender_id INTEGER NOT NULL,
            receiver_type VARCHAR(20) NOT NULL, -- 'admin' or 'company'
            receiver_id INTEGER NOT NULL,
            message_text TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Employees table (now with roles)
        CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            branch_id INTEGER REFERENCES branches(id),
            role_id INTEGER REFERENCES employee_roles(id),
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            full_name VARCHAR(100) NOT NULL,
            profile_pic_url TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Tasks table (updated for branch assignment)
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            company_id INTEGER REFERENCES companies(id),
            branch_id INTEGER REFERENCES branches(id),
            employee_id INTEGER REFERENCES employees(id),
            task_description TEXT NOT NULL,
            due_date DATE,
            is_completed BOOLEAN DEFAULT FALSE,
            completed_by_id INTEGER REFERENCES employees(id),
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Task Assignments for tracking branch-level task completions
        CREATE TABLE IF NOT EXISTS task_assignments (
            id SERIAL PRIMARY KEY,
            task_id INTEGER REFERENCES tasks(id),
            employee_id INTEGER REFERENCES employees(id),
            is_completed BOOLEAN DEFAULT FALSE,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, employee_id)
        );
        
        -- Daily reports table (unchanged)
        CREATE TABLE IF NOT EXISTS daily_reports (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER REFERENCES employees(id),
            report_date DATE NOT NULL,
            report_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Insert default employee roles if they don't exist
        INSERT INTO employee_roles (role_name, role_level, company_id)
        SELECT 'Manager', 1, id FROM companies
        WHERE NOT EXISTS (
            SELECT 1 FROM employee_roles WHERE role_name = 'Manager' AND company_id = companies.id
        );
        
        INSERT INTO employee_roles (role_name, role_level, company_id)
        SELECT 'Asst. Manager', 2, id FROM companies
        WHERE NOT EXISTS (
            SELECT 1 FROM employee_roles WHERE role_name = 'Asst. Manager' AND company_id = companies.id
        );
        
        INSERT INTO employee_roles (role_name, role_level, company_id)
        SELECT 'General Employee', 3, id FROM companies
        WHERE NOT EXISTS (
            SELECT 1 FROM employee_roles WHERE role_name = 'General Employee' AND company_id = companies.id
        );
        
        -- Set existing employees to General Employee role by default
        UPDATE employees e
        SET role_id = r.id
        FROM employee_roles r
        JOIN branches b ON r.company_id = b.company_id
        WHERE e.branch_id = b.id AND r.role_name = 'General Employee' AND e.role_id IS NULL;
        '''))
        conn.commit()

#########################################
# DATA MODELS
#########################################

class CompanyModel:
    """Company data operations"""
    
    @staticmethod
    def get_all_companies(conn):
        """Get all companies from the database."""
        result = conn.execute(text('''
        SELECT id, company_name, username, profile_pic_url, is_active, created_at 
        FROM companies
        ORDER BY company_name
        '''))
        return result.fetchall()
    
    @staticmethod
    def get_active_companies(conn):
        """Get all active companies."""
        result = conn.execute(text('''
        SELECT id, company_name FROM companies 
        WHERE is_active = TRUE
        ORDER BY company_name
        '''))
        return result.fetchall()
    
    @staticmethod
    def get_company_by_id(conn, company_id):
        """Get company data by ID."""
        result = conn.execute(text('''
        SELECT company_name, username, profile_pic_url, is_active
        FROM companies
        WHERE id = :company_id
        '''), {'company_id': company_id})
        return result.fetchone()
    
    @staticmethod
    def add_company(conn, company_name, username, password, profile_pic_url):
        """Add a new company to the database."""
        default_pic = "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
        
        conn.execute(text('''
        INSERT INTO companies (company_name, username, password, profile_pic_url, is_active)
        VALUES (:company_name, :username, :password, :profile_pic_url, TRUE)
        '''), {
            'company_name': company_name,
            'username': username,
            'password': password,
            'profile_pic_url': profile_pic_url if profile_pic_url else default_pic
        })
        conn.commit()
    
    @staticmethod
    def update_company_status(conn, company_id, is_active):
        """Activate or deactivate a company and all its branches and employees."""
        # Update company status
        conn.execute(text('UPDATE companies SET is_active = :is_active WHERE id = :id'), 
                    {'id': company_id, 'is_active': is_active})
        
        # Update all branches for this company
        conn.execute(text('''
        UPDATE branches 
        SET is_active = :is_active 
        WHERE company_id = :company_id
        '''), {'company_id': company_id, 'is_active': is_active})
        
        # Update all employees in all branches of this company
        conn.execute(text('''
        UPDATE employees 
        SET is_active = :is_active 
        WHERE branch_id IN (SELECT id FROM branches WHERE company_id = :company_id)
        '''), {'company_id': company_id, 'is_active': is_active})
        
        conn.commit()
    
    @staticmethod
    def reset_password(conn, company_id, new_password):
        """Reset a company's password."""
        conn.execute(text('UPDATE companies SET password = :password WHERE id = :id'), 
                    {'id': company_id, 'password': new_password})
        conn.commit()
    
    @staticmethod
    def update_profile(conn, company_id, company_name, profile_pic_url):
        """Update company profile information."""
        conn.execute(text('''
        UPDATE companies
        SET company_name = :company_name, profile_pic_url = :profile_pic_url
        WHERE id = :company_id
        '''), {
            'company_name': company_name,
            'profile_pic_url': profile_pic_url,
            'company_id': company_id
        })
        conn.commit()
    
    @staticmethod
    def verify_password(conn, company_id, current_password):
        """Verify company's current password."""
        result = conn.execute(text('''
        SELECT COUNT(*)
        FROM companies
        WHERE id = :company_id AND password = :current_password
        '''), {'company_id': company_id, 'current_password': current_password})
        return result.fetchone()[0] > 0


class BranchModel:
    """Branch data operations"""
    
    @staticmethod
    def get_all_branches(conn):
        """Get all branches with company information."""
        result = conn.execute(text('''
        SELECT b.id, b.branch_name, b.location, b.branch_head, b.is_active, 
               c.company_name, c.id as company_id, b.is_main_branch,
               p.branch_name as parent_branch_name, p.id as parent_branch_id
        FROM branches b
        JOIN companies c ON b.company_id = c.id
        LEFT JOIN branches p ON b.parent_branch_id = p.id
        ORDER BY c.company_name, b.is_main_branch DESC, b.branch_name
        '''))
        return result.fetchall()
    
    @staticmethod
    def get_company_branches(conn, company_id):
        """Get all branches for a specific company."""
        result = conn.execute(text('''
        SELECT b.id, b.branch_name, b.location, b.branch_head, b.is_active,
               b.is_main_branch, b.parent_branch_id,
               p.branch_name as parent_branch_name
        FROM branches b
        LEFT JOIN branches p ON b.parent_branch_id = p.id
        WHERE b.company_id = :company_id
        ORDER BY b.is_main_branch DESC, b.branch_name
        '''), {'company_id': company_id})
        return result.fetchall()
    
    @staticmethod
    def get_branch_by_id(conn, branch_id):
        """Get branch details by ID."""
        result = conn.execute(text('''
        SELECT b.id, b.branch_name, b.location, b.branch_head, b.is_active,
               b.is_main_branch, b.parent_branch_id, b.company_id,
               p.branch_name as parent_branch_name
        FROM branches b
        LEFT JOIN branches p ON b.parent_branch_id = p.id
        WHERE b.id = :branch_id
        '''), {'branch_id': branch_id})
        return result.fetchone()
    
    @staticmethod
    def get_parent_branches(conn, company_id, exclude_branch_id=None):
        """Get all possible parent branches for a company (for creating sub-branches)."""
        query = '''
        SELECT id, branch_name 
        FROM branches
        WHERE company_id = :company_id AND is_active = TRUE
        '''
        
        params = {'company_id': company_id}
        
        if exclude_branch_id:
            query += ' AND id != :exclude_branch_id'
            params['exclude_branch_id'] = exclude_branch_id
        
        query += ' ORDER BY is_main_branch DESC, branch_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_active_branches(conn, company_id=None):
        """Get all active branches, optionally filtered by company."""
        query = '''
        SELECT b.id, b.branch_name, c.company_name
        FROM branches b
        JOIN companies c ON b.company_id = c.id
        WHERE b.is_active = TRUE AND c.is_active = TRUE
        '''
        
        params = {}
        if company_id:
            query += ' AND b.company_id = :company_id'
            params = {'company_id': company_id}
        
        query += ' ORDER BY c.company_name, b.is_main_branch DESC, b.branch_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def create_main_branch(conn, company_id, branch_name, location, branch_head):
        """Create a main branch for a company."""
        conn.execute(text('''
        INSERT INTO branches (company_id, branch_name, location, branch_head, is_main_branch, parent_branch_id, is_active)
        VALUES (:company_id, :branch_name, :location, :branch_head, TRUE, NULL, TRUE)
        '''), {
            'company_id': company_id,
            'branch_name': branch_name,
            'location': location,
            'branch_head': branch_head
        })
        conn.commit()
    
    @staticmethod
    def create_sub_branch(conn, company_id, parent_branch_id, branch_name, location, branch_head):
        """Create a sub-branch under a parent branch."""
        conn.execute(text('''
        INSERT INTO branches (company_id, parent_branch_id, branch_name, location, branch_head, is_main_branch, is_active)
        VALUES (:company_id, :parent_branch_id, :branch_name, :location, :branch_head, FALSE, TRUE)
        '''), {
            'company_id': company_id,
            'parent_branch_id': parent_branch_id,
            'branch_name': branch_name,
            'location': location,
            'branch_head': branch_head
        })
        conn.commit()
    
    @staticmethod
    def update_branch(conn, branch_id, branch_name, location, branch_head, parent_branch_id=None):
        """Update branch details."""
        query = '''
        UPDATE branches 
        SET branch_name = :branch_name, location = :location, branch_head = :branch_head
        '''
        
        params = {
            'branch_id': branch_id,
            'branch_name': branch_name,
            'location': location,
            'branch_head': branch_head
        }
        
        # Only update parent_branch_id if provided and branch is not a main branch
        if parent_branch_id is not None:
            result = conn.execute(text('SELECT is_main_branch FROM branches WHERE id = :branch_id'), 
                                 {'branch_id': branch_id})
            is_main_branch = result.fetchone()[0]
            
            if not is_main_branch:
                query += ', parent_branch_id = :parent_branch_id'
                params['parent_branch_id'] = parent_branch_id
        
        query += ' WHERE id = :branch_id'
        
        conn.execute(text(query), params)
        conn.commit()
    
    @staticmethod
    def update_branch_status(conn, branch_id, is_active):
        """Update branch active status and update related employees status too."""
        with conn.begin():
            # Update branch status
            conn.execute(text('''
            UPDATE branches 
            SET is_active = :is_active
            WHERE id = :branch_id
            '''), {'branch_id': branch_id, 'is_active': is_active})
            
            # Update employees in this branch
            conn.execute(text('''
            UPDATE employees 
            SET is_active = :is_active
            WHERE branch_id = :branch_id
            '''), {'branch_id': branch_id, 'is_active': is_active})
        
    @staticmethod
    def get_branch_employees(conn, branch_id):
        """Get all employees for a specific branch."""
        result = conn.execute(text('''
        SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active, r.role_name, r.role_level
        FROM employees e
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.branch_id = :branch_id
        ORDER BY r.role_level, e.full_name
        '''), {'branch_id': branch_id})
        return result.fetchall()
    
    @staticmethod
    def get_employee_count_by_branch(conn, company_id):
        """Get employee count for each branch of a company."""
        result = conn.execute(text('''
        SELECT b.id, b.branch_name, COUNT(e.id) as employee_count
        FROM branches b
        LEFT JOIN employees e ON b.id = e.branch_id AND e.is_active = TRUE
        WHERE b.company_id = :company_id
        GROUP BY b.id, b.branch_name
        ORDER BY b.is_main_branch DESC, b.branch_name
        '''), {'company_id': company_id})
        return result.fetchall()
    
    @staticmethod
    def get_subbranches(conn, parent_branch_id):
        """Get all sub-branches of a branch."""
        result = conn.execute(text('''
        SELECT id, branch_name, is_active
        FROM branches
        WHERE parent_branch_id = :parent_branch_id
        ORDER BY branch_name
        '''), {'parent_branch_id': parent_branch_id})
        return result.fetchall()


class EmployeeModel:
    """Employee data operations"""
    
    @staticmethod
    def get_all_employees(conn, company_id=None):
        """Get all employees with optional company filter.
        
        Args:
            conn: Database connection
            company_id: Optional company ID filter
            
        Returns:
            List of employees with branch and role info
        """
        query = '''
        SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active,
               b.branch_name, c.company_name, r.role_name, r.role_level, b.id as branch_id
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN companies c ON b.company_id = c.id
        JOIN employee_roles r ON e.role_id = r.id
        '''
        
        params = {}
        if company_id:
            query += ' WHERE b.company_id = :company_id'
            params = {'company_id': company_id}
        
        query += ' ORDER BY c.company_name, b.branch_name, r.role_level, e.full_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_branch_employees(conn, branch_id):
        """Get all employees for a specific branch.
        
        Args:
            conn: Database connection
            branch_id: ID of the branch
            
        Returns:
            List of employees with role info
        """
        result = conn.execute(text('''
        SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active, 
               r.role_name, r.role_level, r.id as role_id
        FROM employees e
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.branch_id = :branch_id
        ORDER BY r.role_level, e.full_name
        '''), {'branch_id': branch_id})
        return result.fetchall()
    
    @staticmethod
    def get_active_employees(conn, company_id=None, branch_id=None, role_level=None):
        """Get active employees with optional filters.
        
        Args:
            conn: Database connection
            company_id: Optional company ID filter
            branch_id: Optional branch ID filter
            role_level: Optional role level filter
            
        Returns:
            List of active employees
        """
        query = '''
        SELECT e.id, e.full_name, b.branch_name, c.company_name, r.role_name
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN companies c ON b.company_id = c.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.is_active = TRUE 
          AND b.is_active = TRUE
          AND c.is_active = TRUE
        '''
        
        params = {}
        
        if company_id:
            query += ' AND c.id = :company_id'
            params['company_id'] = company_id
        
        if branch_id:
            query += ' AND b.id = :branch_id'
            params['branch_id'] = branch_id
        
        if role_level:
            query += ' AND r.role_level = :role_level'
            params['role_level'] = role_level
        
        query += ' ORDER BY b.branch_name, r.role_level, e.full_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_employee_by_id(conn, employee_id):
        """Get detailed employee data by ID.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            
        Returns:
            Employee details including branch and role info
        """
        result = conn.execute(text('''
        SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active,
               b.id as branch_id, b.branch_name, r.id as role_id, r.role_name, 
               c.id as company_id
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN employee_roles r ON e.role_id = r.id
        JOIN companies c ON b.company_id = c.id
        WHERE e.id = :employee_id
        '''), {'employee_id': employee_id})
        return result.fetchone()
    
    @staticmethod
    def add_employee(conn, branch_id, role_id, username, password, full_name, profile_pic_url):
        """Add a new employee.
        
        Args:
            conn: Database connection
            branch_id: ID of the branch
            role_id: ID of the role
            username: Username for login
            password: Password for login
            full_name: Full name of employee
            profile_pic_url: URL to profile picture
        """
        default_pic = "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
        
        conn.execute(text('''
        INSERT INTO employees (branch_id, role_id, username, password, full_name, profile_pic_url, is_active)
        VALUES (:branch_id, :role_id, :username, :password, :full_name, :profile_pic_url, TRUE)
        '''), {
            'branch_id': branch_id,
            'role_id': role_id,
            'username': username,
            'password': password,
            'full_name': full_name,
            'profile_pic_url': profile_pic_url if profile_pic_url else default_pic
        })
        conn.commit()
    
    @staticmethod
    def update_employee_status(conn, employee_id, is_active):
        """Activate or deactivate an employee.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            is_active: New active status
        """
        conn.execute(text('UPDATE employees SET is_active = :is_active WHERE id = :id'), 
                    {'id': employee_id, 'is_active': is_active})
        conn.commit()
    
    @staticmethod
    def update_employee_role(conn, employee_id, role_id):
        """Update employee's role.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            role_id: New role ID
        """
        conn.execute(text('''
        UPDATE employees
        SET role_id = :role_id
        WHERE id = :employee_id
        '''), {
            'employee_id': employee_id,
            'role_id': role_id
        })
        conn.commit()
    
    @staticmethod
    def update_employee_branch(conn, employee_id, branch_id):
        """Transfer employee to different branch.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            branch_id: New branch ID
        """
        conn.execute(text('''
        UPDATE employees
        SET branch_id = :branch_id
        WHERE id = :employee_id
        '''), {
            'employee_id': employee_id,
            'branch_id': branch_id
        })
        conn.commit()
    
    @staticmethod
    def reset_password(conn, employee_id, new_password):
        """Reset an employee's password.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            new_password: New password
        """
        conn.execute(text('UPDATE employees SET password = :password WHERE id = :id'), 
                    {'id': employee_id, 'password': new_password})
        conn.commit()
    
    @staticmethod
    def update_profile(conn, employee_id, full_name, profile_pic_url):
        """Update employee profile information.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            full_name: New full name
            profile_pic_url: New profile picture URL
        """
        conn.execute(text('''
        UPDATE employees
        SET full_name = :full_name, profile_pic_url = :profile_pic_url
        WHERE id = :employee_id
        '''), {
            'full_name': full_name,
            'profile_pic_url': profile_pic_url,
            'employee_id': employee_id
        })
        conn.commit()
    
    @staticmethod
    def verify_password(conn, employee_id, current_password):
        """Verify employee's current password.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            current_password: Password to verify
            
        Returns:
            bool: True if password matches, False otherwise
        """
        result = conn.execute(text('''
        SELECT COUNT(*)
        FROM employees
        WHERE id = :employee_id AND password = :current_password
        '''), {'employee_id': employee_id, 'current_password': current_password})
        return result.fetchone()[0] > 0
    
    @staticmethod
    def update_password(conn, employee_id, new_password):
        """Update an employee's password.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            new_password: New password
        """
        conn.execute(text('''
        UPDATE employees
        SET password = :new_password
        WHERE id = :employee_id
        '''), {
            'employee_id': employee_id,
            'new_password': new_password
        })
        conn.commit()


class MessageModel:
    """Message data operations"""
    
    @staticmethod
    def send_message(conn, sender_type, sender_id, receiver_type, receiver_id, message_text):
        """Send a new message."""
        conn.execute(text('''
        INSERT INTO messages 
        (sender_type, sender_id, receiver_type, receiver_id, message_text, is_read)
        VALUES (:sender_type, :sender_id, :receiver_type, :receiver_id, :message_text, FALSE)
        '''), {
            'sender_type': sender_type,
            'sender_id': sender_id,
            'receiver_type': receiver_type,
            'receiver_id': receiver_id,
            'message_text': message_text
        })
        conn.commit()
    
    @staticmethod
    def mark_as_read(conn, message_id):
        """Mark a message as read."""
        conn.execute(text('UPDATE messages SET is_read = TRUE WHERE id = :id'), 
                    {'id': message_id})
        conn.commit()
    
    @staticmethod
    def get_messages_for_admin(conn):
        """Get all messages for admin."""
        result = conn.execute(text('''
        SELECT m.id, m.sender_type, m.sender_id, m.message_text, m.is_read, m.created_at,
               CASE WHEN m.sender_type = 'company' THEN c.company_name ELSE 'Admin' END as sender_name
        FROM messages m
        LEFT JOIN companies c ON m.sender_type = 'company' AND m.sender_id = c.id
        WHERE m.receiver_type = 'admin'
        ORDER BY m.created_at DESC
        '''))
        return result.fetchall()
    
    @staticmethod
    def get_messages_for_company(conn, company_id):
        """Get all messages for a specific company."""
        result = conn.execute(text('''
        SELECT m.id, m.sender_type, m.sender_id, m.message_text, m.is_read, m.created_at,
               CASE WHEN m.sender_type = 'admin' THEN 'Admin' ELSE c.company_name END as sender_name
        FROM messages m
        LEFT JOIN companies c ON m.sender_type = 'company' AND m.sender_id = c.id
        WHERE (m.receiver_type = 'company' AND m.receiver_id = :company_id)
           OR (m.sender_type = 'company' AND m.sender_id = :company_id)
        ORDER BY m.created_at DESC
        '''), {'company_id': company_id})
        return result.fetchall()


class RoleModel:
    """Employee role data operations"""
    
    @staticmethod
    def get_all_roles(conn, company_id):
        """Get all roles for a company.
        
        Args:
            conn: Database connection
            company_id: ID of the company
            
        Returns:
            List of roles (id, name, level)
        """
        result = conn.execute(text('''
        SELECT id, role_name, role_level
        FROM employee_roles
        WHERE company_id = :company_id
        ORDER BY role_level
        '''), {'company_id': company_id})
        return result.fetchall()
    
    @staticmethod
    def get_role_by_id(conn, role_id):
        """Get role details by ID.
        
        Args:
            conn: Database connection
            role_id: ID of the role
            
        Returns:
            Role details (id, name, level, company_id)
        """
        result = conn.execute(text('''
        SELECT id, role_name, role_level, company_id
        FROM employee_roles
        WHERE id = :role_id
        '''), {'role_id': role_id})
        return result.fetchone()
    
    @staticmethod
    def create_role(conn, company_id, role_name, role_level):
        """Create a new role.
        
        Args:
            conn: Database connection
            company_id: ID of the company
            role_name: Name of the role
            role_level: Level of the role (lower number = higher rank)
        """
        conn.execute(text('''
        INSERT INTO employee_roles (company_id, role_name, role_level)
        VALUES (:company_id, :role_name, :role_level)
        '''), {
            'company_id': company_id,
            'role_name': role_name,
            'role_level': role_level
        })
        conn.commit()
    
    @staticmethod
    def update_role(conn, role_id, role_name, role_level):
        """Update role details.
        
        Args:
            conn: Database connection
            role_id: ID of the role
            role_name: New name for the role
            role_level: New level for the role
        """
        conn.execute(text('''
        UPDATE employee_roles
        SET role_name = :role_name, role_level = :role_level
        WHERE id = :role_id
        '''), {
            'role_id': role_id,
            'role_name': role_name,
            'role_level': role_level
        })
        conn.commit()
    
    @staticmethod
    def delete_role(conn, role_id, replacement_role_id):
        """Delete a role and reassign employees to another role.
        
        Args:
            conn: Database connection
            role_id: ID of the role to delete
            replacement_role_id: ID of the role to assign employees to
        """
        with conn.begin():
            # First reassign all employees with this role
            conn.execute(text('''
            UPDATE employees
            SET role_id = :replacement_role_id
            WHERE role_id = :role_id
            '''), {
                'role_id': role_id,
                'replacement_role_id': replacement_role_id
            })
            
            # Then delete the role
            conn.execute(text('''
            DELETE FROM employee_roles
            WHERE id = :role_id
            '''), {'role_id': role_id})
    
    @staticmethod
    def get_manager_roles(conn, company_id):
        """Get roles that are considered management (Manager and Asst. Manager).
        
        Args:
            conn: Database connection
            company_id: ID of the company
            
        Returns:
            List of management role IDs
        """
        result = conn.execute(text('''
        SELECT id 
        FROM employee_roles
        WHERE company_id = :company_id AND role_level <= 2
        '''), {'company_id': company_id})
        return [row[0] for row in result.fetchall()]
    
    @staticmethod
    def initialize_default_roles(conn, company_id):
        """Initialize default roles for a new company.
        
        Args:
            conn: Database connection
            company_id: ID of the company
        """
        # Check if roles already exist for this company
        result = conn.execute(text('''
        SELECT COUNT(*) FROM employee_roles WHERE company_id = :company_id
        '''), {'company_id': company_id})
        
        if result.fetchone()[0] == 0:
            # Create default roles
            default_roles = [
                ('Manager', 1),
                ('Asst. Manager', 2),
                ('General Employee', 3)
            ]
            
            for role_name, role_level in default_roles:
                conn.execute(text('''
                INSERT INTO employee_roles (company_id, role_name, role_level)
                VALUES (:company_id, :role_name, :role_level)
                '''), {
                    'company_id': company_id,
                    'role_name': role_name,
                    'role_level': role_level
                })
            
            conn.commit()


class ReportModel:
    """Daily report data operations with advanced filtering"""
    
    @staticmethod
    def get_employee_reports(conn, employee_id, start_date, end_date):
        """Get reports for a specific employee within a date range.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            start_date: Start date for filtering
            end_date: End date for filtering
            
        Returns:
            List of reports
        """
        result = conn.execute(text('''
        SELECT id, report_date, report_text
        FROM daily_reports
        WHERE employee_id = :employee_id
        AND report_date BETWEEN :start_date AND :end_date
        ORDER BY report_date DESC
        '''), {'employee_id': employee_id, 'start_date': start_date, 'end_date': end_date})
        return result.fetchall()
    
    @staticmethod
    def get_branch_reports(conn, branch_id, start_date, end_date, role_id=None):
        """Get reports for all employees in a branch within a date range.
        
        Args:
            conn: Database connection
            branch_id: ID of the branch
            start_date: Start date for filtering
            end_date: End date for filtering
            role_id: Optional role ID for filtering
            
        Returns:
            List of reports with employee info
        """
        query = '''
        SELECT dr.id, e.full_name, r.role_name, dr.report_date, dr.report_text, dr.created_at
        FROM daily_reports dr
        JOIN employees e ON dr.employee_id = e.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.branch_id = :branch_id
        AND dr.report_date BETWEEN :start_date AND :end_date
        '''
        
        params = {
            'branch_id': branch_id, 
            'start_date': start_date, 
            'end_date': end_date
        }
        
        if role_id:
            query += ' AND e.role_id = :role_id'
            params['role_id'] = role_id
        
        query += ' ORDER BY dr.report_date DESC, r.role_level, e.full_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_company_reports(conn, company_id, start_date, end_date, branch_id=None, role_id=None):
        """Get reports for all employees in a company within a date range.
        
        Args:
            conn: Database connection
            company_id: ID of the company
            start_date: Start date for filtering
            end_date: End date for filtering
            branch_id: Optional branch ID for filtering
            role_id: Optional role ID for filtering
            
        Returns:
            List of reports with employee and branch info
        """
        query = '''
        SELECT dr.id, e.full_name, r.role_name, b.branch_name, dr.report_date, dr.report_text, dr.created_at
        FROM daily_reports dr
        JOIN employees e ON dr.employee_id = e.id
        JOIN branches b ON e.branch_id = b.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE b.company_id = :company_id
        AND dr.report_date BETWEEN :start_date AND :end_date
        '''
        
        params = {
            'company_id': company_id, 
            'start_date': start_date, 
            'end_date': end_date
        }
        
        if branch_id:
            query += ' AND e.branch_id = :branch_id'
            params['branch_id'] = branch_id
        
        if role_id:
            query += ' AND e.role_id = :role_id'
            params['role_id'] = role_id
        
        query += ' ORDER BY dr.report_date DESC, b.branch_name, r.role_level, e.full_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_all_reports(conn, start_date, end_date, employee_name=None):
        """Get all reports with optional employee filter.
        
        Args:
            conn: Database connection
            start_date: Start date for filtering
            end_date: End date for filtering
            employee_name: Optional employee name filter
            
        Returns:
            List of reports with employee info
        """
        query = '''
        SELECT e.full_name, dr.report_date, dr.report_text, dr.id, e.id as employee_id
        FROM daily_reports dr
        JOIN employees e ON dr.employee_id = e.id
        WHERE dr.report_date BETWEEN :start_date AND :end_date
        '''
        
        params = {'start_date': start_date, 'end_date': end_date}
        
        if employee_name and employee_name != "All Employees":
            query += ' AND e.full_name = :employee_name'
            params['employee_name'] = employee_name
        
        query += ' ORDER BY dr.report_date DESC, e.full_name'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def add_report(conn, employee_id, report_date, report_text):
        """Add a new report.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            report_date: Date of the report
            report_text: Content of the report
        """
        conn.execute(text('''
        INSERT INTO daily_reports (employee_id, report_date, report_text)
        VALUES (:employee_id, :report_date, :report_text)
        '''), {
            'employee_id': employee_id,
            'report_date': report_date,
            'report_text': report_text
        })
        conn.commit()
    
    @staticmethod
    def update_report(conn, report_id, report_date, report_text):
        """Update an existing report.
        
        Args:
            conn: Database connection
            report_id: ID of the report
            report_date: New date for the report
            report_text: New content for the report
        """
        conn.execute(text('''
        UPDATE daily_reports 
        SET report_text = :report_text, report_date = :report_date, created_at = CURRENT_TIMESTAMP
        WHERE id = :id
        '''), {
            'report_text': report_text,
            'report_date': report_date,
            'id': report_id
        })
        conn.commit()
    
    @staticmethod
    def check_report_exists(conn, employee_id, report_date):
        """Check if a report already exists for the given date.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            report_date: Date to check
            
        Returns:
            Report ID if exists, None otherwise
        """
        result = conn.execute(text('''
        SELECT id FROM daily_reports 
        WHERE employee_id = :employee_id AND report_date = :report_date
        '''), {'employee_id': employee_id, 'report_date': report_date})
        return result.fetchone()


class TaskModel:
    """Task data operations with branch and employee assignment support"""
    
    @staticmethod
    def create_task(conn, company_id, task_description, due_date, branch_id=None, employee_id=None):
        """Create a new task with branch or employee assignment.
        
        Args:
            conn: Database connection
            company_id: ID of the company creating the task
            task_description: Description of the task
            due_date: Due date for the task
            branch_id: Optional branch ID for branch-level assignment
            employee_id: Optional employee ID for direct assignment
            
        Returns:
            int: ID of the created task
        """
        with conn.begin():
            # Insert task record
            result = conn.execute(text('''
            INSERT INTO tasks (company_id, branch_id, employee_id, task_description, due_date, is_completed)
            VALUES (:company_id, :branch_id, :employee_id, :task_description, :due_date, FALSE)
            RETURNING id
            '''), {
                'company_id': company_id,
                'branch_id': branch_id,
                'employee_id': employee_id,
                'task_description': task_description,
                'due_date': due_date
            })
            
            task_id = result.fetchone()[0]
            
            # If assigned to a branch, create assignments for all branch employees
            if branch_id and not employee_id:
                # Get all active employees in the branch
                employees = conn.execute(text('''
                SELECT id FROM employees
                WHERE branch_id = :branch_id AND is_active = TRUE
                '''), {'branch_id': branch_id}).fetchall()
                
                # Create task assignments for each employee
                for emp in employees:
                    conn.execute(text('''
                    INSERT INTO task_assignments (task_id, employee_id, is_completed)
                    VALUES (:task_id, :employee_id, FALSE)
                    '''), {
                        'task_id': task_id,
                        'employee_id': emp[0]
                    })
            
            return task_id
    
    @staticmethod
    def get_tasks_for_company(conn, company_id, status_filter=None):
        """Get all tasks for a company with optional status filter.
        
        Args:
            conn: Database connection
            company_id: ID of the company
            status_filter: Optional status filter ('All', 'Pending', 'Completed')
            
        Returns:
            List of tasks with branch and employee info
        """
        query = '''
        SELECT t.id, t.task_description, t.due_date, t.is_completed, 
               t.completed_at, t.created_at, t.branch_id, t.employee_id,
               CASE 
                   WHEN t.branch_id IS NOT NULL THEN b.branch_name 
                   WHEN t.employee_id IS NOT NULL THEN e.full_name
                   ELSE 'Unassigned'
               END as assignee_name,
               CASE
                   WHEN t.branch_id IS NOT NULL THEN 'branch'
                   WHEN t.employee_id IS NOT NULL THEN 'employee'
                   ELSE 'unassigned'
               END as assignee_type,
               ce.full_name as completed_by_name
        FROM tasks t
        LEFT JOIN branches b ON t.branch_id = b.id
        LEFT JOIN employees e ON t.employee_id = e.id
        LEFT JOIN employees ce ON t.completed_by_id = ce.id
        WHERE t.company_id = :company_id
        '''
        
        params = {'company_id': company_id}
        
        if status_filter == "Pending":
            query += ' AND t.is_completed = FALSE'
        elif status_filter == "Completed":
            query += ' AND t.is_completed = TRUE'
        
        query += ' ORDER BY t.due_date ASC NULLS LAST, t.created_at DESC'
        
        result = conn.execute(text(query), params)
        return result.fetchall()
    
    @staticmethod
    def get_branch_task_progress(conn, task_id):
        """Get progress of a branch-level task.
        
        Args:
            conn: Database connection
            task_id: ID of the task
            
        Returns:
            Dict with total, completed counts and employee completion status
        """
        # Get task information
        task_info = conn.execute(text('''
        SELECT branch_id FROM tasks WHERE id = :task_id
        '''), {'task_id': task_id}).fetchone()
        
        if not task_info or not task_info[0]:
            return None  # Not a branch task
        
        # Get completion counts
        counts = conn.execute(text('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN is_completed THEN 1 ELSE 0 END) as completed
        FROM task_assignments
        WHERE task_id = :task_id
        '''), {'task_id': task_id}).fetchone()
        
        # Get individual employee statuses
        employee_statuses = conn.execute(text('''
        SELECT ta.employee_id, e.full_name, ta.is_completed, r.role_name, r.role_level,
               ta.completed_at
        FROM task_assignments ta
        JOIN employees e ON ta.employee_id = e.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE ta.task_id = :task_id
        ORDER BY r.role_level, e.full_name
        '''), {'task_id': task_id}).fetchall()
        
        return {
            'total': counts[0],
            'completed': counts[1],
            'employee_statuses': employee_statuses
        }
    
    @staticmethod
    def mark_task_completed(conn, task_id, employee_id):
        """Mark a task as completed by an employee.
        
        For branch tasks, this marks the employee's assignment as completed.
        For individual tasks, this marks the entire task as completed.
        
        Args:
            conn: Database connection
            task_id: ID of the task
            employee_id: ID of the employee completing the task
            
        Returns:
            bool: True if entire task is now complete, False otherwise
        """
        now = datetime.datetime.now()
        
        with conn.begin():
            # Get task information
            task = conn.execute(text('''
            SELECT branch_id, employee_id, is_completed 
            FROM tasks 
            WHERE id = :task_id
            '''), {'task_id': task_id}).fetchone()
            
            if not task:
                return False
            
            # If already completed, do nothing
            if task[2]:
                return True
            
            # If branch task, update the employee's assignment
            if task[0]:  # branch_id is not None
                # Update the assignment
                conn.execute(text('''
                UPDATE task_assignments
                SET is_completed = TRUE, completed_at = :now
                WHERE task_id = :task_id AND employee_id = :employee_id
                '''), {
                    'task_id': task_id,
                    'employee_id': employee_id,
                    'now': now
                })
                
                # Check employee role level
                employee_role = conn.execute(text('''
                SELECT r.role_level 
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.id = :employee_id
                '''), {'employee_id': employee_id}).fetchone()
                
                is_manager = employee_role and employee_role[0] <= 2  # Manager or Asst. Manager
                
                # If employee is a manager or assistant manager, complete the entire task
                if is_manager:
                    conn.execute(text('''
                    UPDATE tasks
                    SET is_completed = TRUE, completed_at = :now, completed_by_id = :employee_id
                    WHERE id = :task_id
                    '''), {
                        'task_id': task_id,
                        'employee_id': employee_id,
                        'now': now
                    })
                    return True
                
                # Otherwise, check if all assignments are complete
                all_complete = conn.execute(text('''
                SELECT COUNT(*) = 0
                FROM task_assignments
                WHERE task_id = :task_id AND is_completed = FALSE
                '''), {'task_id': task_id}).fetchone()[0]
                
                if all_complete:
                    conn.execute(text('''
                    UPDATE tasks
                    SET is_completed = TRUE, completed_at = :now, completed_by_id = :employee_id
                    WHERE id = :task_id
                    '''), {
                        'task_id': task_id,
                        'employee_id': employee_id,
                        'now': now
                    })
                    return True
                
                return False
                
            # If direct employee task, complete it
            elif task[1] == employee_id:  # task assigned directly to this employee
                conn.execute(text('''
                UPDATE tasks
                SET is_completed = TRUE, completed_at = :now, completed_by_id = :employee_id
                WHERE id = :task_id
                '''), {
                    'task_id': task_id,
                    'employee_id': employee_id,
                    'now': now
                })
                return True
            
            return False
    
    @staticmethod
    def get_tasks_for_employee(conn, employee_id, status_filter=None):
        """Get tasks assigned to an employee.
        
        Includes both direct tasks and branch-level tasks.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            status_filter: Optional status filter ('All', 'Pending', 'Completed')
            
        Returns:
            List of tasks with type and completion status
        """
        # Get employee's branch
        emp_info = conn.execute(text('''
        SELECT branch_id FROM employees WHERE id = :employee_id
        '''), {'employee_id': employee_id}).fetchone()
        
        if not emp_info:
            return []
        
        branch_id = emp_info[0]
        
        # Get directly assigned tasks
        direct_query = '''
        SELECT t.id, t.task_description, t.due_date, t.is_completed, 
               t.completed_at, t.created_at, 'direct' as task_type,
               NULL as assignment_id, t.is_completed as assignment_completed
        FROM tasks t
        WHERE t.employee_id = :employee_id
        '''
        
        if status_filter == "Pending":
            direct_query += ' AND t.is_completed = FALSE'
        elif status_filter == "Completed":
            direct_query += ' AND t.is_completed = TRUE'
        
        # Get branch-level tasks
        branch_query = '''
        SELECT t.id, t.task_description, t.due_date, t.is_completed, 
               t.completed_at, t.created_at, 'branch' as task_type,
               ta.id as assignment_id, ta.is_completed as assignment_completed
        FROM tasks t
        JOIN task_assignments ta ON t.id = ta.task_id
        WHERE t.branch_id = :branch_id AND ta.employee_id = :employee_id
        '''
        
        if status_filter == "Pending":
            branch_query += ' AND ta.is_completed = FALSE'
        elif status_filter == "Completed":
            branch_query += ' AND ta.is_completed = TRUE'
        
        # Combine queries
        query = f'''
        {direct_query}
        UNION ALL
        {branch_query}
        ORDER BY due_date ASC NULLS LAST, created_at DESC
        '''
        
        result = conn.execute(text(query), {
            'employee_id': employee_id,
            'branch_id': branch_id
        })
        
        return result.fetchall()
    
    @staticmethod
    def reopen_task(conn, task_id):
        """Reopen a completed task.
        
        Args:
            conn: Database connection
            task_id: ID of the task
        """
        with conn.begin():
            # First reopen the main task
            conn.execute(text('''
            UPDATE tasks
            SET is_completed = FALSE, completed_at = NULL, completed_by_id = NULL
            WHERE id = :task_id
            '''), {'task_id': task_id})
            
            # Then reopen all assignments
            conn.execute(text('''
            UPDATE task_assignments
            SET is_completed = FALSE, completed_at = NULL
            WHERE task_id = :task_id
            '''), {'task_id': task_id})
    
    @staticmethod
    def delete_task(conn, task_id):
        """Delete a task and all its assignments.
        
        Args:
            conn: Database connection
            task_id: ID of the task
        """
        with conn.begin():
            # First delete all assignments
            conn.execute(text('''
            DELETE FROM task_assignments
            WHERE task_id = :task_id
            '''), {'task_id': task_id})
            
            # Then delete the task
            conn.execute(text('''
            DELETE FROM tasks
            WHERE id = :task_id
            '''), {'task_id': task_id})
    
    @staticmethod
    def add_task(conn, employee_id, task_description, due_date):
        """Add a new task directly to an employee.
        
        Args:
            conn: Database connection
            employee_id: ID of the employee
            task_description: Description of the task
            due_date: Due date for the task
        """
        conn.execute(text('''
        INSERT INTO tasks (employee_id, task_description, due_date, is_completed)
        VALUES (:employee_id, :task_description, :due_date, FALSE)
        '''), {
            'employee_id': employee_id,
            'task_description': task_description,
            'due_date': due_date
        })
        conn.commit()
    
    @staticmethod
    def update_task_status(conn, task_id, is_completed):
        """Update a task's completion status.
        
        Args:
            conn: Database connection
            task_id: ID of the task
            is_completed: New completion status
        """
        conn.execute(text('''
        UPDATE tasks SET is_completed = :is_completed WHERE id = :id
        '''), {'id': task_id, 'is_completed': is_completed})
        conn.commit()


#########################################
# UTILITY FUNCTIONS
#########################################

class RolePermissions:
    """Define role-based permissions and access controls"""
    
    # Role level definitions (lower number = higher authority)
    MANAGER = 1
    ASST_MANAGER = 2
    GENERAL_EMPLOYEE = 3
    
    @staticmethod
    def get_role_level(role_name):
        """Convert role name to role level."""
        role_map = {
            "Manager": RolePermissions.MANAGER,
            "Asst. Manager": RolePermissions.ASST_MANAGER,
            "General Employee": RolePermissions.GENERAL_EMPLOYEE
        }
        return role_map.get(role_name, RolePermissions.GENERAL_EMPLOYEE)
    
    @staticmethod
    def get_role_name(role_level):
        """Convert role level to role name."""
        role_map = {
            RolePermissions.MANAGER: "Manager",
            RolePermissions.ASST_MANAGER: "Asst. Manager",
            RolePermissions.GENERAL_EMPLOYEE: "General Employee"
        }
        return role_map.get(role_level, "General Employee")
    
    @staticmethod
    def can_create_employees(user_role_level):
        """Check if the role can create employee accounts."""
        return user_role_level <= RolePermissions.ASST_MANAGER  # Manager and Asst. Manager can create
    
    @staticmethod
    def can_assign_tasks_to(user_role_level, target_role_level):
        """Check if user role can assign tasks to target role."""
        if user_role_level == RolePermissions.MANAGER:
            # Manager can assign to Asst. Manager and General Employee
            return target_role_level >= RolePermissions.ASST_MANAGER
        elif user_role_level == RolePermissions.ASST_MANAGER:
            # Asst. Manager can only assign to General Employee
            return target_role_level == RolePermissions.GENERAL_EMPLOYEE
        else:
            # General Employee cannot assign tasks
            return False
    
    @staticmethod
    def can_view_reports_of(user_role_level, target_role_level):
        """Check if user role can view reports from target role."""
        if user_role_level == RolePermissions.MANAGER:
            # Manager can view all reports in their branch
            return True
        elif user_role_level == RolePermissions.ASST_MANAGER:
            # Asst. Manager can view their own and General Employee reports
            return target_role_level >= RolePermissions.ASST_MANAGER
        else:
            # General Employee can only view their own reports
            return user_role_level == target_role_level
    
    @staticmethod
    def can_deactivate_role(user_role_level, target_role_level):
        """Check if user role can deactivate/reactivate target role."""
        if user_role_level == RolePermissions.MANAGER:
            # Manager can deactivate Asst. Manager and General Employee
            return target_role_level > user_role_level
        elif user_role_level == RolePermissions.ASST_MANAGER:
            # Asst. Manager can only deactivate General Employees
            return target_role_level == RolePermissions.GENERAL_EMPLOYEE
        else:
            # General Employee cannot deactivate anyone
            return False


def get_date_range_from_filter(date_filter):
    """Get start and end dates based on a date filter selection.
    
    Args:
        date_filter: String representing the selected date range
        
    Returns:
        tuple: (start_date, end_date)
    """
    today = datetime.date.today()
    
    if date_filter == "Today":
        start_date = today
        end_date = today
    elif date_filter == "This Week":
        start_date = today - datetime.timedelta(days=today.weekday())
        end_date = today
    elif date_filter == "This Month":
        start_date = today.replace(day=1)
        end_date = today
    elif date_filter == "This Year":
        start_date = today.replace(month=1, day=1)
        end_date = today
    else:  # All Time/Reports
        start_date = datetime.date(2000, 1, 1)
        end_date = today
    
    return start_date, end_date


def format_timestamp(timestamp, format_str='%d %b, %Y'):
    """Format a timestamp into a readable string.
    
    Args:
        timestamp: Datetime object
        format_str: Format string (default: '%d %b, %Y')
        
    Returns:
        str: Formatted date string or "No date" if None
    """
    if timestamp:
        return timestamp.strftime(format_str)
    return "No due date"


def calculate_completion_rate(total, completed):
    """Calculate the completion rate as a percentage.
    
    Args:
        total: Total number of items
        completed: Number of completed items
        
    Returns:
        int: Completion rate percentage
    """
    if total == 0:
        return 0
    return round((completed / total) * 100)


def authenticate(engine, username, password):
    """Authenticate a user based on username and password.
    
    Args:
        engine: SQLAlchemy database engine
        username: User's username
        password: User's password
        
    Returns:
        dict: User information if authentication succeeds, None otherwise
    """
    # Check if admin credentials are properly set in Streamlit secrets
    if "admin_username" not in st.secrets or "admin_password" not in st.secrets:
        st.warning("Admin credentials are not properly configured in Streamlit secrets. Please set admin_username and admin_password in .streamlit/secrets.toml")
        return None
    
    # Check if credentials match admin in Streamlit secrets
    admin_username = st.secrets["admin_username"]
    admin_password = st.secrets["admin_password"]
    
    if username == admin_username and password == admin_password:
        return {
            "id": 0,  # Special ID for admin
            "username": username, 
            "full_name": "Administrator", 
            "user_type": "admin",
            "profile_pic_url": "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
        }
    
    # If not admin, check company credentials
    with engine.connect() as conn:
        result = conn.execute(text('''
        SELECT id, company_name, username, profile_pic_url
        FROM companies
        WHERE username = :username AND password = :password AND is_active = TRUE
        '''), {'username': username, 'password': password})
        company = result.fetchone()
    
    if company:
        return {
            "id": company[0], 
            "username": company[2], 
            "full_name": company[1], 
            "user_type": "company",
            "profile_pic_url": company[3]
        }
    
    # If not company, check employee credentials with role information
    with engine.connect() as conn:
        result = conn.execute(text('''
        SELECT e.id, e.username, e.full_name, e.profile_pic_url, 
               b.id as branch_id, b.branch_name, c.id as company_id, c.company_name,
               r.id as role_id, r.role_name, r.role_level
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN companies c ON b.company_id = c.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.username = :username AND e.password = :password 
          AND e.is_active = TRUE AND b.is_active = TRUE AND c.is_active = TRUE
        '''), {'username': username, 'password': password})
        employee = result.fetchone()
    
    if employee:
        return {
            "id": employee[0], 
            "username": employee[1], 
            "full_name": employee[2],
            "user_type": "employee",
            "profile_pic_url": employee[3],
            "branch_id": employee[4],
            "branch_name": employee[5],
            "company_id": employee[6],
            "company_name": employee[7],
            "role_id": employee[8],
            "role_name": employee[9],
            "role_level": employee[10]
        }
    
    return None


def logout():
    """Log out the current user by clearing session state."""
    st.session_state.pop("user", None)
    st.rerun()


def get_custom_css():
    """Return the custom CSS for better UI styling.
    
    Returns:
        str: CSS styles as a string
    """
    return """
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1E88E5;
        margin-bottom: 1rem;
        text-align: center;
    }
    
    .sub-header {
        font-size: 1.8rem;
        font-weight: 600;
        color: #333;
        margin-bottom: 1rem;
    }
    
    .card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1.5rem;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-bottom: 1rem;
    }
    
    .stat-card {
        background-color: #ffffff;
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        text-align: center;
    }
    
    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #1E88E5;
    }
    
    .stat-label {
        font-size: 1rem;
        color: #777;
    }
    
    .login-container {
        max-width: 400px;
        margin: 0 auto;
        padding: 2.5rem;
    }
    
    .login-header {
        text-align: center;
        margin-bottom: 1.5rem;
    }
    
    .stButton > button {
        width: 100%;
        background-color: #1E88E5;
        color: white;
        font-weight: 600;
        height: 2.5rem;
        border-radius: 5px;
    }
    
    .stTextInput > div > div > input {
        height: 2.5rem;
    }
    
    .report-item {
        background-color: #f1f7fe;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        border-left: 4px solid #1E88E5;
    }
    
    .task-item {
        background-color: #f1fff1;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        border-left: 4px solid #4CAF50;
    }
    
    .task-item.completed {
        background-color: #f0f0f0;
        border-left: 4px solid #9e9e9e;
    }
    
    .profile-container {
        display: flex;
        align-items: center;
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    
    .profile-image {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        object-fit: cover;
        border: 3px solid #1E88E5;
    }
</style>
"""


def create_employee_report_pdf(reports, employee_name=None):
    """Generate a PDF report for employee daily reports.
    
    Args:
        reports: List of report data tuples (id, date, text)
        employee_name: Name of the employee (optional)
        
    Returns:
        bytes: PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    # Title
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=16,
        alignment=1,
        spaceAfter=12
    )
    title = f"Work Reports: {employee_name}" if employee_name else "Work Reports"
    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 12))
    
    # Date range
    if reports:
        date_style = ParagraphStyle(
            'DateRange',
            parent=styles['Normal'],
            fontSize=10,
            alignment=1,
            textColor=colors.gray
        )
        min_date = min(report[1] for report in reports).strftime('%d %b %Y')
        max_date = max(report[1] for report in reports).strftime('%d %b %Y')
        elements.append(Paragraph(f"Period: {min_date} to {max_date}", date_style))
        elements.append(Spacer(1, 20))
    
    # Group reports by month
    reports_by_month = {}
    for report in reports:
        month_year = report[1].strftime('%B %Y')
        if month_year not in reports_by_month:
            reports_by_month[month_year] = []
        reports_by_month[month_year].append(report)
    
    # Add each month's reports
    for month, month_reports in reports_by_month.items():
        # Month header
        month_style = ParagraphStyle(
            'Month',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10
        )
        elements.append(Paragraph(month, month_style))
        
        # Reports for the month
        for report in month_reports:
            # Date
            date_style = ParagraphStyle(
                'Date',
                parent=styles['Normal'],
                fontSize=11,
                textColor=colors.blue
            )
            elements.append(Paragraph(report[1].strftime('%A, %d %b %Y'), date_style))
            
            # Report text
            text_style = ParagraphStyle(
                'ReportText',
                parent=styles['Normal'],
                fontSize=10,
                leftIndent=10
            )
            elements.append(Paragraph(report[2], text_style))
            elements.append(Spacer(1, 12))
        
        elements.append(Spacer(1, 10))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def create_branch_report_pdf(reports, branch_name):
    """Generate a PDF report for all employees in a branch.
    
    Args:
        reports: List of report data tuples (id, employee_name, role, date, text, created_at)
        branch_name: Name of the branch
        
    Returns:
        bytes: PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    # Title
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=16,
        alignment=1,
        spaceAfter=12
    )
    elements.append(Paragraph(f"Branch Reports: {branch_name}", title_style))
    elements.append(Spacer(1, 12))
    
    # Date range
    if reports:
        date_style = ParagraphStyle(
            'DateRange',
            parent=styles['Normal'],
            fontSize=10,
            alignment=1,
            textColor=colors.gray
        )
        min_date = min(report[3] for report in reports).strftime('%d %b %Y')
        max_date = max(report[3] for report in reports).strftime('%d %b %Y')
        elements.append(Paragraph(f"Period: {min_date} to {max_date}", date_style))
        elements.append(Spacer(1, 20))
    
    # Group reports by employee and date
    reports_by_employee = {}
    for report in reports:
        employee_name = report[1]
        role_name = report[2]
        
        key = f"{employee_name} ({role_name})"
        if key not in reports_by_employee:
            reports_by_employee[key] = []
        
        reports_by_employee[key].append(report)
    
    # Add each employee's reports
    for employee, emp_reports in reports_by_employee.items():
        # Employee header
        emp_style = ParagraphStyle(
            'Employee',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10
        )
        elements.append(Paragraph(employee, emp_style))
        
        # Group by date
        for report in emp_reports:
            # Date
            date_style = ParagraphStyle(
                'Date',
                parent=styles['Normal'],
                fontSize=11,
                textColor=colors.blue
            )
            elements.append(Paragraph(report[3].strftime('%A, %d %b %Y'), date_style))
            
            # Report text
            text_style = ParagraphStyle(
                'ReportText',
                parent=styles['Normal'],
                fontSize=10,
                leftIndent=10
            )
            elements.append(Paragraph(report[4], text_style))
            elements.append(Spacer(1, 12))
        
        elements.append(Spacer(1, 15))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def create_company_report_pdf(reports, company_name):
    """Generate a PDF report for all branches in a company.
    
    Args:
        reports: List of report data tuples (id, employee_name, role, branch_name, date, text, created_at)
        company_name: Name of the company
        
    Returns:
        bytes: PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.5*inch, rightMargin=0.5*inch)
    styles = getSampleStyleSheet()
    elements = []
    
    # Title
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=16,
        alignment=1,
        spaceAfter=12
    )
    elements.append(Paragraph(f"Company Reports: {company_name}", title_style))
    elements.append(Spacer(1, 12))
    
    # Date range
    if reports:
        date_style = ParagraphStyle(
            'DateRange',
            parent=styles['Normal'],
            fontSize=10,
            alignment=1,
            textColor=colors.gray
        )
        min_date = min(report[4] for report in reports).strftime('%d %b %Y')
        max_date = max(report[4] for report in reports).strftime('%d %b %Y')
        elements.append(Paragraph(f"Period: {min_date} to {max_date}", date_style))
        elements.append(Spacer(1, 20))
    
    # Group reports by branch, then by employee
    reports_by_branch = {}
    for report in reports:
        branch_name = report[3]
        
        if branch_name not in reports_by_branch:
            reports_by_branch[branch_name] = {}
        
        employee_name = report[1]
        role_name = report[2]
        key = f"{employee_name} ({role_name})"
        
        if key not in reports_by_branch[branch_name]:
            reports_by_branch[branch_name][key] = []
        
        reports_by_branch[branch_name][key].append(report)
    
    # Add each branch's reports
    for branch_name, employees in reports_by_branch.items():
        # Branch header
        branch_style = ParagraphStyle(
            'Branch',
            parent=styles['Heading2'],
            fontSize=16,
            spaceAfter=10,
            textColor=colors.blue
        )
        elements.append(Paragraph(f"Branch: {branch_name}", branch_style))
        
        # For each employee in the branch
        for employee_name, emp_reports in employees.items():
            # Employee header
            emp_style = ParagraphStyle(
                'Employee',
                parent=styles['Heading3'],
                fontSize=14,
                spaceAfter=8
            )
            elements.append(Paragraph(employee_name, emp_style))
            
            # Group by date
            emp_reports_by_date = {}
            for report in emp_reports:
                date_str = report[4].strftime('%Y-%m-%d')
                if date_str not in emp_reports_by_date:
                    emp_reports_by_date[date_str] = report
            
            # Add each report
            for date_str, report in sorted(emp_reports_by_date.items(), reverse=True):
                # Date
                date_style = ParagraphStyle(
                    'Date',
                    parent=styles['Normal'],
                    fontSize=11,
                    textColor=colors.darkblue
                )
                elements.append(Paragraph(report[4].strftime('%A, %d %b %Y'), date_style))
                
                # Report text
                text_style = ParagraphStyle(
                    'ReportText',
                    parent=styles['Normal'],
                    fontSize=10,
                    leftIndent=10
                )
                elements.append(Paragraph(report[5], text_style))
                elements.append(Spacer(1, 10))
            
            elements.append(Spacer(1, 10))
        
        elements.append(Spacer(1, 20))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def create_role_report_pdf(reports, role_name, company_name):
    """Generate a PDF report for all employees of a specific role.
    
    Args:
        reports: List of report data tuples with employee and branch info
        role_name: Name of the role
        company_name: Name of the company
        
    Returns:
        bytes: PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    # Title
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=16,
        alignment=1,
        spaceAfter=12
    )
    elements.append(Paragraph(f"{role_name} Reports - {company_name}", title_style))
    elements.append(Spacer(1, 12))
    
    # Date range
    if reports:
        date_style = ParagraphStyle(
            'DateRange',
            parent=styles['Normal'],
            fontSize=10,
            alignment=1,
            textColor=colors.gray
        )
        min_date = min(report[4] for report in reports).strftime('%d %b %Y')
        max_date = max(report[4] for report in reports).strftime('%d %b %Y')
        elements.append(Paragraph(f"Period: {min_date} to {max_date}", date_style))
        elements.append(Spacer(1, 20))
    
    # Group reports by employee and branch
    reports_by_employee = {}
    for report in reports:
        employee_name = report[1]
        branch_name = report[3]
        
        key = f"{employee_name} ({branch_name})"
        if key not in reports_by_employee:
            reports_by_employee[key] = []
        
        reports_by_employee[key].append(report)
    
    # Add each employee's reports
    for employee, emp_reports in reports_by_employee.items():
        # Employee header
        emp_style = ParagraphStyle(
            'Employee',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10
        )
        elements.append(Paragraph(employee, emp_style))
        
        # Group by date
        emp_reports_by_date = {}
        for report in emp_reports:
            date_str = report[4].strftime('%Y-%m-%d')
            if date_str not in emp_reports_by_date:
                emp_reports_by_date[date_str] = report
        
        # Add each report
        for date_str, report in sorted(emp_reports_by_date.items(), reverse=True):
            # Date
            date_style = ParagraphStyle(
                'Date',
                parent=styles['Normal'],
                fontSize=11,
                textColor=colors.blue
            )
            elements.append(Paragraph(report[4].strftime('%A, %d %b %Y'), date_style))
            
            # Report text
            text_style = ParagraphStyle(
                'ReportText',
                parent=styles['Normal'],
                fontSize=10,
                leftIndent=10
            )
            elements.append(Paragraph(report[5], text_style))
            elements.append(Spacer(1, 10))
        
        elements.append(Spacer(1, 15))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


#########################################
# UI COMPONENTS
#########################################

def display_profile_header(user):
    """Display user profile header with image and name.
    
    Args:
        user: User dict with profile information
    """
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        st.markdown('<div class="profile-container">', unsafe_allow_html=True)
        try:
            st.image(user["profile_pic_url"], width=80, clamp=True, output_format="auto", 
                    channels="RGB", use_container_width=False)
        except:
            st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", 
                    width=80, use_container_width=False)
        
        user_type = "Administrator" if user.get("is_admin", False) else "Employee"
        st.markdown(f'''
        <div>
            <h3>{user["full_name"]}</h3>
            <p>{user_type}</p>
        </div>
        ''', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)


def display_stats_card(value, label):
    """Display a statistics card with value and label.
    
    Args:
        value: The statistic value to display
        label: The label for the statistic
    """
    st.markdown('<div class="stat-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="stat-value">{value}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="stat-label">{label}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

def view_branch_employee_reports(engine, branch_id, role_level):
    """View reports based on role permissions.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Reports")
    
    # Current employee ID
    employee_id = st.session_state.user["id"]
    
    # Filter options
    col1, col2 = st.columns(2)
    
    with col1:
        if role_level == RolePermissions.MANAGER:
            employee_filter_options = ["All Employees", "By Role", "Individual Employee"]
        else:  # Asst. Manager
            employee_filter_options = ["General Employees", "My Reports"]
        
        employee_filter = st.selectbox(
            "View",
            employee_filter_options
        )
    
    with col2:
        date_options = [
            "Today",
            "This Week",
            "This Month", 
            "Last Month",
            "Last 3 Months",
            "Custom Range"
        ]
        
        date_filter = st.selectbox("Date Range", date_options)
    
    # Date range calculation
    today = datetime.date.today()
    
    if date_filter == "Today":
        start_date = end_date = today
    elif date_filter == "This Week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif date_filter == "This Month":
        start_date = today.replace(day=1)
        end_date = today
    elif date_filter == "Last Month":
        last_month = today.month - 1 if today.month > 1 else 12
        last_month_year = today.year if today.month > 1 else today.year - 1
        start_date = datetime.date(last_month_year, last_month, 1)
        # Calculate last day of last month
        if last_month == 12:
            end_date = datetime.date(last_month_year, last_month, 31)
        else:
            end_date = datetime.date(last_month_year, last_month + 1, 1) - timedelta(days=1)
    elif date_filter == "Last 3 Months":
        start_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        start_date = (start_date - timedelta(days=1)).replace(day=1)
        end_date = today
    elif date_filter == "Custom Range":
        cols = st.columns(2)
        with cols[0]:
            start_date = st.date_input("Start Date", today - timedelta(days=30))
        with cols[1]:
            end_date = st.date_input("End Date", today)
    
    # Additional role-specific filters
    selected_role = None
    selected_employee = None
    
    if role_level == RolePermissions.MANAGER and employee_filter == "By Role":
        with engine.connect() as conn:
            result = conn.execute(text('''
            SELECT DISTINCT r.role_name
            FROM employee_roles r
            JOIN employees e ON e.role_id = r.id
            WHERE e.branch_id = :branch_id
            ORDER BY r.role_level
            '''), {'branch_id': branch_id})
            
            roles = [row[0] for row in result.fetchall()]
        
        selected_role = st.selectbox("Select Role", roles)
    
    elif ((role_level == RolePermissions.MANAGER and employee_filter == "Individual Employee") or
          (role_level == RolePermissions.ASST_MANAGER and employee_filter == "General Employees")):
        with engine.connect() as conn:
            if role_level == RolePermissions.MANAGER:
                # Managers can select any employee
                result = conn.execute(text('''
                SELECT e.id, e.full_name, r.role_name
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id
                ORDER BY r.role_level, e.full_name
                '''), {'branch_id': branch_id})
            else:
                # Asst. Managers can only select General Employees
                result = conn.execute(text('''
                SELECT e.id, e.full_name, r.role_name
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND r.role_level = :general_level
                ORDER BY e.full_name
                '''), {
                    'branch_id': branch_id,
                    'general_level': RolePermissions.GENERAL_EMPLOYEE
                })
            
            employees = result.fetchall()
            
            if not employees:
                st.warning("No employees found")
            else:
                # Create employee options
                employee_options = {f"{emp[1]} ({emp[2]})": emp[0] for emp in employees}
                selected_employee_name = st.selectbox("Select Employee", list(employee_options.keys()))
                selected_employee = employee_options[selected_employee_name]
    
    # Fetch reports based on filters
    with engine.connect() as conn:
        if role_level == RolePermissions.MANAGER:
            if employee_filter == "All Employees":
                # All branch employees
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC, r.role_level, e.full_name
                '''), {
                    'branch_id': branch_id,
                    'start_date': start_date,
                    'end_date': end_date
                })
            elif employee_filter == "By Role" and selected_role:
                # By role
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND r.role_name = :role_name 
                  AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC, e.full_name
                '''), {
                    'branch_id': branch_id,
                    'role_name': selected_role,
                    'start_date': start_date,
                    'end_date': end_date
                })
            elif employee_filter == "Individual Employee" and selected_employee:
                # Individual employee
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE dr.employee_id = :employee_id AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC
                '''), {
                    'employee_id': selected_employee,
                    'start_date': start_date,
                    'end_date': end_date
                })
            else:
                result = None
        elif role_level == RolePermissions.ASST_MANAGER:
            if employee_filter == "General Employees" and selected_employee:
                # Individual General Employee
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE dr.employee_id = :employee_id AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC
                '''), {
                    'employee_id': selected_employee,
                    'start_date': start_date,
                    'end_date': end_date
                })
            elif employee_filter == "My Reports":
                # Own reports
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE dr.employee_id = :employee_id AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC
                '''), {
                    'employee_id': employee_id,
                    'start_date': start_date,
                    'end_date': end_date
                })
            else:
                # All General Employees (default)
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND r.role_level = :general_level
                  AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC, e.full_name
                '''), {
                    'branch_id': branch_id,
                    'general_level': RolePermissions.GENERAL_EMPLOYEE,
                    'start_date': start_date,
                    'end_date': end_date
                })
        
        reports = result.fetchall() if result else []
    
    if not reports:
        st.info("No reports found for the selected criteria")
    else:
        st.success(f"Found {len(reports)} reports")
        
        # Create PDF download button
        if st.button("Download as PDF"):
            # Create PDF (implementation would be in utils/pdf_generator.py)
            # For now, just show a placeholder message
            st.info("PDF download feature will be implemented")
        
        # Group reports by date
        reports_by_date = {}
        for report in reports:
            date_str = report[2].strftime('%Y-%m-%d')
            if date_str not in reports_by_date:
                reports_by_date[date_str] = []
            reports_by_date[date_str].append(report)
        
        # Display reports by date
        for date_str, date_reports in sorted(reports_by_date.items(), reverse=True):
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            with st.expander(f"{date_obj.strftime('%A, %d %b %Y')} ({len(date_reports)} reports)", expanded=False):
                for report in date_reports:
                    name = report[0]
                    role = report[1]
                    text = report[3]
                    
                    st.markdown(f"""
                    <div class="report-item">
                        <div><strong>{name}</strong> ({role})</div>
                        <p>{text}</p>
                    </div>
                    """, unsafe_allow_html=True)


def view_employee_tasks(engine, employee_id):
    """View and act on tasks assigned to the employee.
    
    Args:
        engine: SQLAlchemy database engine
        employee_id: Employee ID
    """
    st.subheader("My Tasks")
    
    # Filter options
    status_filter = st.selectbox(
        "Status",
        ["All Tasks", "Pending", "Completed"],
        key="my_task_status_filter"
    )
    
    # Fetch tasks
    with engine.connect() as conn:
        query = '''
        SELECT t.id, t.task_description, t.due_date, t.is_completed, t.created_at
        FROM tasks t
        WHERE t.employee_id = :employee_id
        '''
        
        params = {'employee_id': employee_id}
        
        # Add status filter
        if status_filter == "Pending":
            query += ' AND t.is_completed = FALSE'
        elif status_filter == "Completed":
            query += ' AND t.is_completed = TRUE'
        
        # Sort by due date
        query += ' ORDER BY t.due_date ASC, t.created_at DESC'
        
        # Execute query
        result = conn.execute(text(query), params)
        tasks = result.fetchall()
    
    if not tasks:
        st.info("No tasks found")
    else:
        # Display tasks
        for task in tasks:
            task_id = task[0]
            description = task[1]
            due_date = task[2].strftime('%d %b, %Y') if task[2] else "No due date"
            is_completed = task[3]
            
            # Task card styling
            status_class = "completed" if is_completed else ""
            
            st.markdown(f'''
            <div class="task-item {status_class}">
                <div style="display: flex; justify-content: space-between;">
                    <span><strong>Due:</strong> {due_date}</span>
                    <span style="font-weight: 600; color: {'#9e9e9e' if is_completed else '#4CAF50'};">
                        {"Completed" if is_completed else "Pending"}
                    </span>
                </div>
                <p>{description}</p>
            </div>
            ''', unsafe_allow_html=True)
            
            # Actions based on status
            if not is_completed:
                if st.button(f"Mark as Completed", key=f"complete_my_task_{task_id}"):
                    with engine.connect() as conn:
                        conn.execute(text('''
                        UPDATE tasks SET is_completed = TRUE 
                        WHERE id = :id
                        '''), {'id': task_id})
                        conn.commit()
                    st.success("Task marked as completed")
                    st.rerun()


def view_my_reports(engine, employee_id):
    """View personal reports with filtering.
    
    Args:
        engine: SQLAlchemy database engine
        employee_id: Employee ID
    """
    st.subheader("My Reports")
    
    # Filter options
    date_options = [
        "All Reports",
        "This Month",
        "Last Month",
        "Custom Range"
    ]
    
    date_filter = st.selectbox("Date Range", date_options)
    
    # Date range calculation
    today = datetime.date.today()
    
    if date_filter == "This Month":
        start_date = today.replace(day=1)
        end_date = today
    elif date_filter == "Last Month":
        last_month = today.month - 1 if today.month > 1 else 12
        last_month_year = today.year if today.month > 1 else today.year - 1
        start_date = datetime.date(last_month_year, last_month, 1)
        # Calculate last day of last month
        if last_month == 12:
            end_date = datetime.date(last_month_year, last_month, 31)
        else:
            end_date = datetime.date(last_month_year, last_month + 1, 1) - timedelta(days=1)
    elif date_filter == "Custom Range":
        cols = st.columns(2)
        with cols[0]:
            start_date = st.date_input("Start Date", today - timedelta(days=30))
        with cols[1]:
            end_date = st.date_input("End Date", today)
    else:  # All Reports
        start_date = datetime.date(2000, 1, 1)  # A date far in the past
        end_date = today
    
    # Fetch reports
    with engine.connect() as conn:
        result = conn.execute(text('''
        SELECT dr.id, dr.report_date, dr.report_text
        FROM daily_reports dr
        WHERE dr.employee_id = :employee_id AND dr.report_date BETWEEN :start_date AND :end_date
        ORDER BY dr.report_date DESC
        '''), {
            'employee_id': employee_id,
            'start_date': start_date,
            'end_date': end_date
        })
        
        reports = result.fetchall()
    
    if not reports:
        st.info("No reports found for the selected period")
    else:
        st.success(f"Found {len(reports)} reports")
        
        # Create PDF download button
        if st.button("Download as PDF"):
            # Create PDF (implementation would be in utils/pdf_generator.py)
            # For now, just show a placeholder message
            st.info("PDF download feature will be implemented")
        
        # Display reports
        for report in reports:
            report_id = report[0]
            report_date = report[1]
            report_text = report[2]
            
            st.markdown(f'''
            <div class="report-item">
                <div><strong>{report_date.strftime('%A, %d %b %Y')}</strong></div>
                <p>{report_text}</p>
            </div>
            ''', unsafe_allow_html=True)


def edit_employee_profile(engine, employee_id):
    """Allow employee to edit their profile.
    
    Args:
        engine: SQLAlchemy database engine
        employee_id: Employee ID
    """
    st.subheader("My Profile")
    
    # Fetch current employee data
    with engine.connect() as conn:
        result = conn.execute(text('''
        SELECT e.username, e.full_name, e.profile_pic_url,
               b.branch_name, r.role_name
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.id = :employee_id
        '''), {'employee_id': employee_id})
        
        employee_data = result.fetchone()
    
    if not employee_data:
        st.error("Could not retrieve your profile information. Please try again later.")
        return
    
    username, current_full_name, current_pic_url, branch_name, role_name = employee_data
    
    # Display current info
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.write("Current Picture:")
        try:
            st.image(current_pic_url, width=150)
        except:
            st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", width=150)
    
    with col2:
        st.write(f"**Username:** {username} (cannot be changed)")
        st.write(f"**Branch:** {branch_name}")
        st.write(f"**Role:** {role_name}")
    
    # Form for updating profile
    with st.form("update_profile_form"):
        new_full_name = st.text_input("Full Name", value=current_full_name)
        new_profile_pic_url = st.text_input("Profile Picture URL", value=current_pic_url or "")
        
        # Password change
        st.subheader("Change Password")
        current_password = st.text_input("Current Password", type="password")
        new_password = st.text_input("New Password", type="password")
        confirm_password = st.text_input("Confirm New Password", type="password")
        
        submitted = st.form_submit_button("Update Profile")
        
        if submitted:
            updates_made = False
            
            # Update profile info if changed
            if new_full_name != current_full_name or new_profile_pic_url != current_pic_url:
                with engine.connect() as conn:
                    conn.execute(text('''
                    UPDATE employees
                    SET full_name = :full_name, profile_pic_url = :profile_pic_url
                    WHERE id = :employee_id
                    '''), {
                        'full_name': new_full_name,
                        'profile_pic_url': new_profile_pic_url,
                        'employee_id': employee_id
                    })
                    conn.commit()
                
                # Update session state
                st.session_state.user["full_name"] = new_full_name
                
                updates_made = True
                st.success("Profile information updated successfully")
            
            # Update password if requested
            if current_password or new_password or confirm_password:
                if not current_password:
                    st.error("Please enter your current password to change it")
                elif not new_password:
                    st.error("Please enter a new password")
                elif new_password != confirm_password:
                    st.error("New passwords do not match")
                else:
                    # Verify current password
                    with engine.connect() as conn:
                        result = conn.execute(text('''
                        SELECT COUNT(*) FROM employees
                        WHERE id = :employee_id AND password = :current_password
                        '''), {
                            'employee_id': employee_id,
                            'current_password': current_password
                        })
                        
                        if result.fetchone()[0] == 0:
                            st.error("Current password is incorrect")
                        else:
                            # Update password
                            conn.execute(text('''
                            UPDATE employees
                            SET password = :new_password
                            WHERE id = :employee_id
                            '''), {
                                'new_password': new_password,
                                'employee_id': employee_id
                            })
                            conn.commit()
                            
                            updates_made = True
                            st.success("Password updated successfully")
            
            if updates_made:
                st.info("Refreshing in 3 seconds...")
                time.sleep(3)
                st.rerun()


#########################################
# MAIN APPLICATION
#########################################

def setup_page_config():
    """Configure the Streamlit page settings"""
    # Page config
    st.set_page_config(
        page_title="Employee Management System",
        page_icon="👥",
        layout="centered",
        initial_sidebar_state="expanded"
    )
    
    # Apply custom CSS
    st.markdown(get_custom_css(), unsafe_allow_html=True)


def main():
    """Main application entry point"""
    # Set up page configuration
    setup_page_config()
    
    # Initialize database connection
    engine = init_connection()
    
    if engine:
        # Initialize database tables if they don't exist
        init_db(engine)

        # Check if user is logged in
        if "user" not in st.session_state:
            display_login(engine)
        else:
            # Show appropriate dashboard based on user type
            user_type = st.session_state.user.get("user_type", "")
            
            if user_type == "admin":
                admin_dashboard(engine)
            elif user_type == "company":
                company_dashboard(engine)
            elif user_type == "employee":
                # Use the new role-based employee dashboard
                employee_dashboard(engine)
            else:
                st.error("Invalid user type. Please log out and try again.")
                if st.button("Logout"):
                    logout()
    else:
        st.error("Failed to connect to the database. Please check your database configuration.")


if __name__ == "__main__":
    main()
    """Display dashboard overview based on role.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Dashboard Overview")
    
    employee_id = st.session_state.user["id"]
    
    # Statistics row
    col1, col2, col3, col4 = st.columns(4)
    
    with engine.connect() as conn:
        # Task stats
        if role_level == RolePermissions.MANAGER:
            # Get all branch tasks
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks 
            WHERE branch_id = :branch_id AND is_completed = FALSE
            '''), {'branch_id': branch_id})
            pending_tasks = result.fetchone()[0]
        elif role_level == RolePermissions.ASST_MANAGER:
            # Get tasks for general employees plus own tasks
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks 
            WHERE (employee_id IN (
                SELECT id FROM employees WHERE branch_id = :branch_id AND role_id = (
                    SELECT id FROM employee_roles WHERE role_level = 3
                )
            ) OR employee_id = :employee_id) AND is_completed = FALSE
            '''), {'branch_id': branch_id, 'employee_id': employee_id})
            pending_tasks = result.fetchone()[0]
        else:
            # Get own tasks only
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks 
            WHERE employee_id = :employee_id AND is_completed = FALSE
            '''), {'employee_id': employee_id})
            pending_tasks = result.fetchone()[0]
        
        # Personal report stats
        today = datetime.date.today()
        result = conn.execute(text('''
        SELECT COUNT(*) FROM daily_reports 
        WHERE employee_id = :employee_id AND report_date = :today
        '''), {'employee_id': employee_id, 'today': today})
        todays_report = result.fetchone()[0] > 0
        
        # Get employee counts for managers/asst. managers
        if role_level <= RolePermissions.ASST_MANAGER:
            if role_level == RolePermissions.MANAGER:
                result = conn.execute(text('''
                SELECT COUNT(*) FROM employees 
                WHERE branch_id = :branch_id AND is_active = TRUE
                '''), {'branch_id': branch_id})
            else:
                result = conn.execute(text('''
                SELECT COUNT(*) FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND e.is_active = TRUE 
                AND r.role_level = :general_level
                '''), {'branch_id': branch_id, 'general_level': RolePermissions.GENERAL_EMPLOYEE})
            
            employee_count = result.fetchone()[0]
        
        # Get recent activities
        if role_level == RolePermissions.MANAGER:
            # For managers - see all branch activity
            result = conn.execute(text('''
            SELECT e.full_name, r.role_name, dr.report_date, dr.report_text 
            FROM daily_reports dr
            JOIN employees e ON dr.employee_id = e.id
            JOIN employee_roles r ON e.role_id = r.id
            WHERE e.branch_id = :branch_id
            ORDER BY dr.created_at DESC
            LIMIT 3
            '''), {'branch_id': branch_id})
        elif role_level == RolePermissions.ASST_MANAGER:
            # For asst. managers - see own and general employees
            result = conn.execute(text('''
            SELECT e.full_name, r.role_name, dr.report_date, dr.report_text 
            FROM daily_reports dr
            JOIN employees e ON dr.employee_id = e.id
            JOIN employee_roles r ON e.role_id = r.id
            WHERE e.branch_id = :branch_id 
            AND (r.role_level = :general_level OR e.id = :employee_id)
            ORDER BY dr.created_at DESC
            LIMIT 3
            '''), {
                'branch_id': branch_id, 
                'general_level': RolePermissions.GENERAL_EMPLOYEE,
                'employee_id': employee_id
            })
        else:
            # For general employees - see only own
            result = conn.execute(text('''
            SELECT e.full_name, r.role_name, dr.report_date, dr.report_text 
            FROM daily_reports dr
            JOIN employees e ON dr.employee_id = e.id
            JOIN employee_roles r ON e.role_id = r.id
            WHERE e.id = :employee_id
            ORDER BY dr.created_at DESC
            LIMIT 3
            '''), {'employee_id': employee_id})
        
        recent_reports = result.fetchall()
    
    with col1:
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-value">{pending_tasks}</div>
                <div class="stat-label">Pending Tasks</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    with col2:
        report_status = "Submitted" if todays_report else "Not Submitted"
        report_color = "#4CAF50" if todays_report else "#F44336"
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-value" style="color: {report_color};">{report_status}</div>
                <div class="stat-label">Today's Report</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    if role_level <= RolePermissions.ASST_MANAGER:
        with col3:
            st.markdown(
                f"""
                <div class="stat-card">
                    <div class="stat-value">{employee_count}</div>
                    <div class="stat-label">{'Branch Employees' if role_level == 1 else 'General Employees'}</div>
                </div>
                """,
                unsafe_allow_html=True
            )
    
    # Quick actions
    st.subheader("Quick Actions")
    
    # Submit report button
    if not todays_report:
        if st.button("Submit Today's Report"):
            st.session_state.submit_report = True
            st.rerun()
    
    # Report submission form if needed
    if hasattr(st.session_state, 'submit_report') and st.session_state.submit_report:
        with st.form("submit_daily_report"):
            st.subheader("Submit Daily Report")
            report_text = st.text_area("What did you work on today?", height=150)
            
            submitted = st.form_submit_button("Submit Report")
            if submitted:
                if not report_text:
                    st.error("Please enter your report")
                else:
                    with engine.connect() as conn:
                        today = datetime.date.today()
                        
                        # Check if report already exists
                        result = conn.execute(text('''
                        SELECT id FROM daily_reports 
                        WHERE employee_id = :employee_id AND report_date = :today
                        '''), {'employee_id': employee_id, 'today': today})
                        
                        existing = result.fetchone()
                        
                        if existing:
                            # Update existing report
                            conn.execute(text('''
                            UPDATE daily_reports 
                            SET report_text = :report_text, created_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                            '''), {'report_text': report_text, 'id': existing[0]})
                        else:
                            # Create new report
                            conn.execute(text('''
                            INSERT INTO daily_reports (employee_id, report_date, report_text)
                            VALUES (:employee_id, :today, :report_text)
                            '''), {
                                'employee_id': employee_id,
                                'today': today,
                                'report_text': report_text
                            })
                        
                        conn.commit()
                    
                    st.success("Report submitted successfully")
                    del st.session_state.submit_report
                    st.rerun()
    
    # Recent activities
    st.subheader("Recent Reports")
    
    if recent_reports:
        for report in recent_reports:
            name = report[0]
            role = report[1]
            date = report[2].strftime('%d %b, %Y') if report[2] else "Unknown"
            text = report[3]
            
            st.markdown(f"""
            <div class="report-item">
                <div><strong>{name}</strong> ({role}) - {date}</div>
                <p>{text[:150]}{'...' if len(text) > 150 else ''}</p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No recent reports found")


def manage_branch_employees(engine, branch_id, role_level):
    """Manage employees in the branch based on role permissions.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Manage Branch Employees")
    
    # Different tabs based on role
    if role_level == RolePermissions.MANAGER:
        tabs = st.tabs(["All Employees", "Add Employee"])
    else:  # Asst. Manager
        tabs = st.tabs(["General Employees", "Add Employee"])
    
    with tabs[0]:
        # Fetch employees based on role permissions
        with engine.connect() as conn:
            if role_level == RolePermissions.MANAGER:
                # Managers can see all employees in their branch
                result = conn.execute(text('''
                SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active,
                       r.role_name, r.role_level
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id
                ORDER BY r.role_level, e.full_name
                '''), {'branch_id': branch_id})
            else:
                # Asst. Managers can only see General Employees
                result = conn.execute(text('''
                SELECT e.id, e.username, e.full_name, e.profile_pic_url, e.is_active,
                       r.role_name, r.role_level
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND r.role_level = :general_level
                ORDER BY e.full_name
                '''), {
                    'branch_id': branch_id,
                    'general_level': RolePermissions.GENERAL_EMPLOYEE
                })
            
            employees = result.fetchall()
        
        if not employees:
            st.info("No employees found")
        else:
            # Group by role if manager
            if role_level == RolePermissions.MANAGER:
                employees_by_role = {}
                for emp in employees:
                    role = emp[5]
                    if role not in employees_by_role:
                        employees_by_role[role] = []
                    employees_by_role[role].append(emp)
                
                # Display by role
                for role, role_employees in employees_by_role.items():
                    st.subheader(f"{role}s")
                    display_employee_list(engine, role_employees, role_level)
            else:
                # Just display the list for asst. manager
                display_employee_list(engine, employees, role_level)
    
    with tabs[1]:
        # Add employee form - both can add only General Employees
        with st.form("add_employee_form"):
            full_name = st.text_input("Full Name")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            profile_pic_url = st.text_input("Profile Picture URL (optional)")
            
            submitted = st.form_submit_button("Add Employee")
            if submitted:
                if not full_name or not username or not password:
                    st.error("Please fill all required fields")
                else:
                    with engine.connect() as conn:
                        # Check if username already exists
                        result = conn.execute(text('''
                        SELECT COUNT(*) FROM employees WHERE username = :username
                        '''), {'username': username})
                        
                        if result.fetchone()[0] > 0:
                            st.error(f"Username '{username}' already exists")
                        else:
                            try:
                                # Get the General Employee role ID
                                result = conn.execute(text('''
                                SELECT id FROM employee_roles 
                                WHERE role_level = :role_level AND company_id = (
                                    SELECT company_id FROM branches WHERE id = :branch_id
                                )
                                '''), {
                                    'role_level': RolePermissions.GENERAL_EMPLOYEE,
                                    'branch_id': branch_id
                                })
                                
                                role_id = result.fetchone()[0]
                                
                                # Add the employee
                                conn.execute(text('''
                                INSERT INTO employees (branch_id, role_id, username, password, full_name, profile_pic_url, is_active)
                                VALUES (:branch_id, :role_id, :username, :password, :full_name, :profile_pic_url, TRUE)
                                '''), {
                                    'branch_id': branch_id,
                                    'role_id': role_id,
                                    'username': username,
                                    'password': password,
                                    'full_name': full_name,
                                    'profile_pic_url': profile_pic_url if profile_pic_url else "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
                                })
                                
                                conn.commit()
                                st.success(f"Successfully added {full_name} as General Employee")
                            except Exception as e:
                                st.error(f"Error adding employee: {e}")


def display_employee_list(engine, employees, viewer_role_level):
    """Display a list of employees with appropriate actions based on viewer role.
    
    Args:
        engine: SQLAlchemy database engine
        employees: List of employee data
        viewer_role_level: Role level of the person viewing the list
    """
    for employee in employees:
        employee_id = employee[0]
        username = employee[1]
        full_name = employee[2]
        profile_pic_url = employee[3] or "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
        is_active = employee[4]
        role_name = employee[5]
        employee_role_level = employee[6]
        
        # Only show actions if viewer has permission to manage this role
        can_manage = RolePermissions.can_deactivate_role(viewer_role_level, employee_role_level)
        
        cols = st.columns([1, 3, 1] if can_manage else [1, 4])
        
        with cols[0]:
            st.image(profile_pic_url, width=60)
        
        with cols[1]:
            st.write(f"**{full_name}**")
            st.write(f"Username: {username}")
            st.write(f"Status: {'Active' if is_active else 'Inactive'}")
        
        if can_manage:
            with cols[2]:
                if is_active:
                    if st.button("Deactivate", key=f"deactivate_{employee_id}"):
                        with engine.connect() as conn:
                            conn.execute(text('''
                            UPDATE employees SET is_active = FALSE WHERE id = :id
                            '''), {'id': employee_id})
                            conn.commit()
                        st.success(f"Deactivated {full_name}")
                        st.rerun()
                else:
                    if st.button("Activate", key=f"activate_{employee_id}"):
                        with engine.connect() as conn:
                            conn.execute(text('''
                            UPDATE employees SET is_active = TRUE WHERE id = :id
                            '''), {'id': employee_id})
                            conn.commit()
                        st.success(f"Activated {full_name}")
                        st.rerun()
        
        st.markdown("---")


def manage_employee_tasks(engine, branch_id, role_level):
    """Manage tasks based on role permissions.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Task Management")
    
    tabs = st.tabs(["Assign Task", "View Tasks"])
    
    with tabs[0]:
        # Task assignment form
        with st.form("assign_task_form"):
            st.subheader("Assign New Task")
            
            # Get assignable employees based on role
            with engine.connect() as conn:
                if role_level == RolePermissions.MANAGER:
                    # Managers can assign to all branch employees
                    result = conn.execute(text('''
                    SELECT e.id, e.full_name, r.role_name
                    FROM employees e
                    JOIN employee_roles r ON e.role_id = r.id
                    WHERE e.branch_id = :branch_id AND e.is_active = TRUE
                      AND e.id != :current_employee  -- Don't include self
                    ORDER BY r.role_level, e.full_name
                    '''), {
                        'branch_id': branch_id,
                        'current_employee': st.session_state.user["id"]
                    })
                else:
                    # Asst. Managers can only assign to General Employees
                    result = conn.execute(text('''
                    SELECT e.id, e.full_name, r.role_name
                    FROM employees e
                    JOIN employee_roles r ON e.role_id = r.id
                    WHERE e.branch_id = :branch_id AND e.is_active = TRUE
                      AND r.role_level = :general_level
                    ORDER BY e.full_name
                    '''), {
                        'branch_id': branch_id,
                        'general_level': RolePermissions.GENERAL_EMPLOYEE
                    })
                
                employees = result.fetchall()
            
            if not employees:
                st.warning("No eligible employees found to assign tasks")
                st.form_submit_button("Assign Task", disabled=True)
            else:
                # Create employee selection
                employee_options = {}
                for emp in employees:
                    employee_options[f"{emp[1]} ({emp[2]})"] = emp[0]
                
                selected_employee = st.selectbox("Assign to", list(employee_options.keys()))
                task_description = st.text_area("Task Description")
                due_date = st.date_input("Due Date", datetime.date.today() + timedelta(days=1))
                
                submitted = st.form_submit_button("Assign Task")
                
                if submitted:
                    if not task_description:
                        st.error("Please enter a task description")
                    else:
                        # Create task
                        try:
                            with engine.connect() as conn:
                                employee_id = employee_options[selected_employee]
                                
                                conn.execute(text('''
                                INSERT INTO tasks (
                                    branch_id, employee_id, task_description, due_date, is_completed
                                ) VALUES (
                                    :branch_id, :employee_id, :task_description, :due_date, FALSE
                                )
                                '''), {
                                    'branch_id': branch_id,
                                    'employee_id': employee_id,
                                    'task_description': task_description,
                                    'due_date': due_date
                                })
                                
                                conn.commit()
                            
                            st.success(f"Task assigned to {selected_employee.split(' (')[0]}")
                        except Exception as e:
                            st.error(f"Error assigning task: {e}")
    
    with tabs[1]:
        # View tasks based on role
        employee_id = st.session_state.user["id"]
        
        # Filter options
        col1, col2 = st.columns(2)
        
        with col1:
            status_filter = st.selectbox(
                "Status",
                ["All Tasks", "Pending", "Completed"],
                key="task_status_filter"
            )
        
        # Fetch tasks based on role permissions
        with engine.connect() as conn:
            if role_level == RolePermissions.MANAGER:
                # Managers see all branch tasks
                query = '''
                SELECT t.id, e.full_name, r.role_name, t.task_description, 
                       t.due_date, t.is_completed, t.created_at
                FROM tasks t
                JOIN employees e ON t.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE t.branch_id = :branch_id
                '''
                
                params = {'branch_id': branch_id}
                
            elif role_level == RolePermissions.ASST_MANAGER:
                # Asst. Managers see their tasks and General Employee tasks
                query = '''
                SELECT t.id, e.full_name, r.role_name, t.task_description, 
                       t.due_date, t.is_completed, t.created_at
                FROM tasks t
                JOIN employees e ON t.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE (t.employee_id = :employee_id OR
                      (e.branch_id = :branch_id AND r.role_level = :general_level))
                '''
                
                params = {
                    'employee_id': employee_id,
                    'branch_id': branch_id,
                    'general_level': RolePermissions.GENERAL_EMPLOYEE
                }
            
            # Add status filter
            if status_filter == "Pending":
                query += ' AND t.is_completed = FALSE'
            elif status_filter == "Completed":
                query += ' AND t.is_completed = TRUE'
            
            # Sort by due date
            query += ' ORDER BY t.due_date ASC, t.created_at DESC'
            
            # Execute query
            result = conn.execute(text(query), params)
            tasks = result.fetchall()
        
        if not tasks:
            st.info("No tasks found")
        else:
            # Display tasks
            for task in tasks:
                task_id = task[0]
                assigned_to = task[1]
                role_name = task[2]
                description = task[3]
                due_date = task[4].strftime('%d %b, %Y') if task[4] else "No due date"
                is_completed = task[5]
                
                # Task card styling
                status_class = "completed" if is_completed else ""
                
                st.markdown(f'''
                <div class="task-item {status_class}">
                    <div style="display: flex; justify-content: space-between;">
                        <span><strong>Assigned to:</strong> {assigned_to} ({role_name})</span>
                        <span><strong>Due:</strong> {due_date}</span>
                    </div>
                    <p>{description}</p>
                    <div style="text-align: right; font-weight: 600; color: {'#9e9e9e' if is_completed else '#4CAF50'};">
                        {"Completed" if is_completed else "Pending"}
                    </div>
                </div>
                ''', unsafe_allow_html=True)
                
                # Actions based on status
                col1, col2 = st.columns(2)
                
                with col1:
                    if not is_completed:
                        if st.button(f"Mark as Completed", key=f"complete_task_{task_id}"):
                            with engine.connect() as conn:
                                conn.execute(text('''
                                UPDATE tasks SET is_completed = TRUE 
                                WHERE id = :id
                                '''), {'id': task_id})
                                conn.commit()
                            st.success("Task marked as completed")
                            st.rerun()
                
                with col2:
                    if is_completed:
                        if st.button(f"Reopen Task", key=f"reopen_task_{task_id}"):
                            with engine.connect() as conn:
                                conn.execute(text('''
                                UPDATE tasks SET is_completed = FALSE 
                                WHERE id = :id
                                '''), {'id': task_id})
                                conn.commit()
                            st.success("Task reopened")
                            st.rerun()


def view_branch_employee_reports(engine, branch_id, role_level):
    """View reports based on role permissions.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Reports")
    
    # Current employee ID
    employee_id = st.session_state.user["id"]
    
    # Filter options
    col1, col2 = st.columns(2)
    
    with col1:
        if role_level == RolePermissions.MANAGER:
            employee_filter_options = ["All Employees", "By Role", "Individual Employee"]
        else:  # Asst. Manager
            employee_filter_options = ["General Employees", "My Reports"]
        
        employee_filter = st.selectbox(
            "View",
            employee_filter_options
        )
    
    with col2:
        date_options = [
            "Today",
            "This Week",
            "This Month", 
            "Last Month",
            "Last 3 Months",
            "Custom Range"
        ]
        
        date_filter = st.selectbox("Date Range", date_options)
    
    # Date range calculation
    today = datetime.date.today()
    
    if date_filter == "Today":
        start_date = end_date = today
    elif date_filter == "This Week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif date_filter == "This Month":
        start_date = today.replace(day=1)
        end_date = today
    elif date_filter == "Last Month":
        last_month = today.month - 1 if today.month > 1 else 12
        last_month_year = today.year if today.month > 1 else today.year - 1
        start_date = datetime.date(last_month_year, last_month, 1)
        # Calculate last day of last month
        if last_month == 12:
            end_date = datetime.date(last_month_year, last_month, 31)
        else:
            end_date = datetime.date(last_month_year, last_month + 1, 1) - timedelta(days=1)
    elif date_filter == "Last 3 Months":
        start_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        start_date = (start_date - timedelta(days=1)).replace(day=1)
        end_date = today
    elif date_filter == "Custom Range":
        cols = st.columns(2)
        with cols[0]:
            start_date = st.date_input("Start Date", today - timedelta(days=30))
        with cols[1]:
            end_date = st.date_input("End Date", today)
    
    # Additional role-specific filters
    selected_role = None
    selected_employee = None
    
    if role_level == RolePermissions.MANAGER and employee_filter == "By Role":
        with engine.connect() as conn:
            result = conn.execute(text('''
            SELECT DISTINCT r.role_name
            FROM employee_roles r
            JOIN employees e ON e.role_id = r.id
            WHERE e.branch_id = :branch_id
            ORDER BY r.role_level
            '''), {'branch_id': branch_id})
            
            roles = [row[0] for row in result.fetchall()]
        
        selected_role = st.selectbox("Select Role", roles)
    
    elif ((role_level == RolePermissions.MANAGER and employee_filter == "Individual Employee") or
          (role_level == RolePermissions.ASST_MANAGER and employee_filter == "General Employees")):
        with engine.connect() as conn:
            if role_level == RolePermissions.MANAGER:
                # Managers can select any employee
                result = conn.execute(text('''
                SELECT e.id, e.full_name, r.role_name
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id
                ORDER BY r.role_level, e.full_name
                '''), {'branch_id': branch_id})
            else:
                # Asst. Managers can only select General Employees
                result = conn.execute(text('''
                SELECT e.id, e.full_name, r.role_name
                FROM employees e
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND r.role_level = :general_level
                ORDER BY e.full_name
                '''), {
                    'branch_id': branch_id,
                    'general_level': RolePermissions.GENERAL_EMPLOYEE
                })
            
            employees = result.fetchall()
            
            if not employees:
                st.warning("No employees found")
            else:
                # Create employee options
                employee_options = {f"{emp[1]} ({emp[2]})": emp[0] for emp in employees}
                selected_employee_name = st.selectbox("Select Employee", list(employee_options.keys()))
                selected_employee = employee_options[selected_employee_name]
    
    # Fetch reports based on filters
    with engine.connect() as conn:
        if role_level == RolePermissions.MANAGER:
            if employee_filter == "All Employees":
                # All branch employees
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branch_id = :branch_id AND dr.report_date BETWEEN :start_date AND :end_date
                ORDER BY dr.report_date DESC, r.role_level, e.full_name
                '''), {
                    'branch_id': branch_id,
                    'start_date': start_date,
                    'end_date': end_date
                })
            elif employee_filter == "By Role" and selected_role:
                # By role
                result = conn.execute(text('''
                SELECT e.full_name, r.role_name, dr.report_date, dr.report_text
                FROM daily_reports dr
                JOIN employees e ON dr.employee_id = e.id
                JOIN employee_roles r ON e.role_id = r.id
                WHERE e.branchdef view_employee_reports(engine, company_id):
    """View and download reports for a specific employee.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown("### Individual Employee Reports")
    
    # Get active employees
    with engine.connect() as conn:
        employees = EmployeeModel.get_active_employees(conn, company_id)
    
    if not employees:
        st.warning("No active employees found.")
        return
    
    # Employee selection with branch info
    employee_options = {}
    for emp in employees:
        display_name = f"{emp[1]} ({emp[4]}, {emp[2]})"
        employee_options[display_name] = emp[0]
    
    selected_employee = st.selectbox("Select Employee", list(employee_options.keys()))
    employee_id = employee_options[selected_employee]
    employee_name = selected_employee.split(" (")[0]
    
    # Date range filter
    col1, col2 = st.columns(2)
    
    with col1:
        date_options = [
            "This Week",
            "This Month",
            "This Year",
            "All Reports",
            "Custom Range"
        ]
        date_filter = st.selectbox("Date Range", date_options, key="employee_reports_date_filter")
    
    with col2:
        # Custom date range if selected
        if date_filter == "Custom Range":
            today = datetime.date.today()
            start_date = st.date_input("Start Date", today - datetime.timedelta(days=30), key="emp_start_date")
            end_date = st.date_input("End Date", today, key="emp_end_date")
        else:
            # Set default dates based on filter
            start_date, end_date = get_date_range_from_filter(date_filter)
    
    # Fetch reports
    with engine.connect() as conn:
        reports = ReportModel.get_employee_reports(conn, employee_id, start_date, end_date)
    
    if not reports:
        st.info(f"No reports found for {employee_name} in the selected period.")
        return
    
    # Display report stats
    total_reports = len(reports)
    
    st.write(f"Found {total_reports} reports from {employee_name}.")
    
    # Download button
    if st.button("Download as PDF", key="download_employee_reports"):
        pdf = create_employee_report_pdf(reports, employee_name)
        
        # Format date range for filename
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        st.download_button(
            label="Download PDF",
            data=pdf,
            file_name=f"{employee_name}_reports_{start_str}_to_{end_str}.pdf",
            mime="application/pdf"
        )
    
    # Display reports
    for report in sorted(reports, key=lambda x: x[1], reverse=True):
        report_date = report[1]
        report_text = report[2]
        
        st.markdown(f'''
        <div class="report-item">
            <strong>{report_date.strftime('%A, %d %b %Y')}</strong>
            <p>{report_text}</p>
        </div>
        ''', unsafe_allow_html=True)


#########################################
# EMPLOYEE DASHBOARD
#########################################

def employee_dashboard(engine):
    """Role-based employee dashboard.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.title("Employee Dashboard")
    
    # Get employee info with role
    employee_id = st.session_state.user["id"]
    
    with engine.connect() as conn:
        # Fetch employee details including role
        result = conn.execute(text('''
        SELECT e.id, e.full_name, e.username, e.profile_pic_url, 
               b.id as branch_id, b.branch_name, 
               r.id as role_id, r.role_name, r.role_level
        FROM employees e
        JOIN branches b ON e.branch_id = b.id
        JOIN employee_roles r ON e.role_id = r.id
        WHERE e.id = :employee_id
        '''), {'employee_id': employee_id})
        
        employee_details = result.fetchone()
    
    if not employee_details:
        st.error("Could not load employee details. Please log out and try again.")
        if st.button("Logout"):
            logout()
        return
    
    # Extract employee details
    employee_name = employee_details[1]
    branch_id = employee_details[4]
    branch_name = employee_details[5]
    role_name = employee_details[7]
    role_level = employee_details[8]
    
    # Store additional info in session state for use across the app
    st.session_state.user.update({
        "branch_id": branch_id,
        "branch_name": branch_name,
        "role_name": role_name,
        "role_level": role_level
    })
    
    # Display welcome message with role
    st.write(f"Welcome, {employee_name} ({role_name}) - {branch_name} Branch")
    
    # Role-specific navigation
    if role_level == RolePermissions.MANAGER or role_level == RolePermissions.ASST_MANAGER:
        # Manager and Asst. Manager navigation
        tabs = st.tabs(["Dashboard", "Employees", "Tasks", "Reports", "Profile"])
        
        with tabs[0]:
            display_role_dashboard(engine, branch_id, role_level)
        
        with tabs[1]:
            manage_branch_employees(engine, branch_id, role_level)
            
        with tabs[2]:
            manage_employee_tasks(engine, branch_id, role_level)
            
        with tabs[3]:
            view_branch_employee_reports(engine, branch_id, role_level)
            
        with tabs[4]:
            edit_employee_profile(engine, employee_id)
    else:
        # General Employee navigation
        tabs = st.tabs(["Dashboard", "Tasks", "My Reports", "Profile"])
        
        with tabs[0]:
            display_role_dashboard(engine, branch_id, role_level)
            
        with tabs[1]:
            view_employee_tasks(engine, employee_id)
            
        with tabs[2]:
            view_my_reports(engine, employee_id)
            
        with tabs[3]:
            edit_employee_profile(engine, employee_id)
    
    # Logout option
    if st.sidebar.button("Logout"):
        logout()


def display_role_dashboard(engine, branch_id, role_level):
    """Display dashboard overview based on role.
    
    Args:
        engine: SQLAlchemy database engine
        branch_id: Branch ID
        role_level: Employee role level
    """
    st.subheader("Dashboard Overview")
    
    employee_id = st.session_state.user["id"]
    
    # Statistics row
    col1, col2, col3, col4 = st.columns(4)
    
    with engine.connect() as conn:
        # Task stats
        if role_level == RolePermissions.MANAGER:
            # Get all branch tasks
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks 
            WHERE branch_id = :branch_id AND is_completed = FALSE
            '''), {'branch_id': branch_id})
            pending_tasks = result.fetchone()[0]
        elif role_level == RolePermissions.ASST_MANAGER:
            # Get tasks for general employees plus own tasks
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks 
            WHERE (employee_id IN (
                SELECT id FROM employees WHERE branch_id = :branch_id AND role_id = (
                    SELECT id FROM employee_roles WHERE role_level = 3
                )
            ) OR employee_id = :employee_id) AND is_completed = FALSE
            '''), {'branch_id': branch_id, 'employee_id': employee_id})
            pending_tasks = result.fetchone()[0]
        else:
            # Get own tasks only
            result = conn.execute(text('''
            SELECT COUNT(*) FROM tasks #########################################
# COMPANY - EMPLOYEES
#########################################

def manage_company_employees(engine):
    """Manage employees with role assignment and branch transfers.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Manage Employees</h2>', unsafe_allow_html=True)
    
    company_id = st.session_state.user["id"]
    
    tabs = st.tabs(["Employee List", "Add New Employee", "Update Role", "Transfer Branch"])
    
    with tabs[0]:
        display_company_employee_list(engine, company_id)
    
    with tabs[1]:
        add_new_company_employee(engine, company_id)
        
    with tabs[2]:
        update_employee_role(engine, company_id)
        
    with tabs[3]:
        transfer_employee_branch(engine, company_id)
    
    # Handle edit form if an employee is selected
    if hasattr(st.session_state, 'edit_employee'):
        edit_employee(engine, company_id)


def display_company_employee_list(engine, company_id):
    """Display the list of employees grouped by branch and role.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    # Get all employees for this company
    with engine.connect() as conn:
        employees = EmployeeModel.get_all_employees(conn, company_id)
    
    if not employees:
        st.info("No employees found. Add employees using the 'Add New Employee' tab.")
        return
    
    # Group employees by branch
    employees_by_branch = {}
    for employee in employees:
        branch_name = employee[5]
        if branch_name not in employees_by_branch:
            employees_by_branch[branch_name] = []
        employees_by_branch[branch_name].append(employee)
    
    st.write(f"Total employees: {len(employees)}")
    
    # Display employees by branch
    for branch_name, branch_employees in employees_by_branch.items():
        with st.expander(f"📍 {branch_name} ({len(branch_employees)} employees)", expanded=False):
            # Group branch employees by role
            employees_by_role = {}
            for employee in branch_employees:
                role_name = employee[7]
                if role_name not in employees_by_role:
                    employees_by_role[role_name] = []
                employees_by_role[role_name].append(employee)
            
            # Display employees by role
            for role_name, role_employees in sorted(employees_by_role.items(), 
                                                   key=lambda x: next((e[8] for e in x[1]), 999)):
                st.markdown(f"**{role_name}s:**")
                
                for employee in role_employees:
                    employee_id = employee[0]
                    username = employee[1]
                    full_name = employee[2]
                    profile_pic_url = employee[3]
                    is_active = employee[4]
                    branch_id = employee[9]
                    
                    cols = st.columns([1, 3, 1])
                    with cols[0]:
                        try:
                            st.image(profile_pic_url, width=60)
                        except:
                            st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", width=60)
                    
                    with cols[1]:
                        st.write(f"**{full_name}** (@{username})")
                        st.write(f"Role: {role_name} | Status: {'Active' if is_active else 'Inactive'}")
                    
                    with cols[2]:
                        if st.button("Actions", key=f"actions_{employee_id}"):
                            st.session_state.employee_actions = employee_id
                            st.rerun()
                    
                    # Show actions if selected
                    if hasattr(st.session_state, 'employee_actions') and st.session_state.employee_actions == employee_id:
                        action_cols = st.columns(4)
                        
                        with action_cols[0]:
                            if st.button("Edit", key=f"edit_{employee_id}"):
                                st.session_state.edit_employee = {
                                    'id': employee_id,
                                    'username': username,
                                    'full_name': full_name,
                                    'profile_pic_url': profile_pic_url,
                                    'is_active': is_active
                                }
                                st.rerun()
                        
                        with action_cols[1]:
                            if is_active:
                                if st.button("Deactivate", key=f"deactivate_{employee_id}"):
                                    with engine.connect() as conn:
                                        EmployeeModel.update_employee_status(conn, employee_id, False)
                                    st.success(f"Deactivated employee: {full_name}")
                                    st.rerun()
                            else:
                                if st.button("Activate", key=f"activate_{employee_id}"):
                                    with engine.connect() as conn:
                                        EmployeeModel.update_employee_status(conn, employee_id, True)
                                    st.success(f"Activated employee: {full_name}")
                                    st.rerun()
                        
                        with action_cols[2]:
                            if st.button("Reset Password", key=f"reset_{employee_id}"):
                                new_password = "employee123"  # Default reset password
                                with engine.connect() as conn:
                                    EmployeeModel.reset_password(conn, employee_id, new_password)
                                st.success(f"Password reset to '{new_password}' for {full_name}")
                        
                        with action_cols[3]:
                            if st.button("Close", key=f"close_{employee_id}"):
                                del st.session_state.employee_actions
                                st.rerun()


def add_new_company_employee(engine, company_id):
    """Form to add a new employee with role and branch assignment.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown("### Add New Employee")
    
    # Get active branches
    with engine.connect() as conn:
        branches = BranchModel.get_active_branches(conn, company_id)
        roles = RoleModel.get_all_roles(conn, company_id)
    
    if not branches:
        st.warning("No active branches found. Please add and activate branches first.")
        return
    
    if not roles:
        st.warning("No roles defined. Please contact your administrator.")
        return
    
    # Convert to dictionaries for selection
    branch_options = {branch[1]: branch[0] for branch in branches}
    role_options = {role[1]: role[0] for role in roles}
    
    with st.form("add_employee_form"):
        st.subheader("Employee Details")
        
        full_name = st.text_input("Full Name", help="Employee's full name")
        username = st.text_input("Username", help="Username for employee login")
        password = st.text_input("Password", type="password", help="Initial password")
        profile_pic_url = st.text_input("Profile Picture URL", help="Link to employee profile picture")
        
        st.subheader("Assignment")
        selected_branch = st.selectbox("Branch", list(branch_options.keys()))
        selected_role = st.selectbox("Role", list(role_options.keys()))
        
        submitted = st.form_submit_button("Add Employee")
        if submitted:
            if not username or not password or not full_name:
                st.error("Please fill all required fields")
            else:
                # Check if username already exists
                with engine.connect() as conn:
                    result = conn.execute(text('SELECT COUNT(*) FROM employees WHERE username = :username'), 
                                          {'username': username})
                    count = result.fetchone()[0]
                    
                    if count > 0:
                        st.error(f"Username '{username}' already exists")
                    else:
                        # Get branch and role IDs
                        branch_id = branch_options[selected_branch]
                        role_id = role_options[selected_role]
                        
                        # Insert new employee
                        try:
                            with engine.connect() as conn:
                                EmployeeModel.add_employee(
                                    conn, 
                                    branch_id, 
                                    role_id, 
                                    username, 
                                    password, 
                                    full_name, 
                                    profile_pic_url
                                )
                            st.success(f"Successfully added employee: {full_name}")
                        except Exception as e:
                            st.error(f"Error adding employee: {e}")


def update_employee_role(engine, company_id):
    """Form to update an employee's role.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown("### Update Employee Role")
    
    # Get employees and roles
    with engine.connect() as conn:
        employees = EmployeeModel.get_all_employees(conn, company_id)
        roles = RoleModel.get_all_roles(conn, company_id)
    
    if not employees:
        st.warning("No employees found.")
        return
    
    if not roles:
        st.warning("No roles defined. Please contact your administrator.")
        return
    
    # Create employee options
    employee_options = {}
    for emp in employees:
        display_name = f"{emp[2]} ({emp[7]}, {emp[5]})"
        employee_options[display_name] = emp[0]
    
    # Create role options
    role_options = {role[1]: role[0] for role in roles}
    
    with st.form("update_role_form"):
        selected_employee = st.selectbox("Select Employee", list(employee_options.keys()))
        selected_role = st.selectbox("New Role", list(role_options.keys()))
        
        submitted = st.form_submit_button("Update Role")
        if submitted:
            employee_id = employee_options[selected_employee]
            role_id = role_options[selected_role]
            
            # Update the employee's role
            try:
                with engine.connect() as conn:
                    EmployeeModel.update_employee_role(conn, employee_id, role_id)
                st.success(f"Successfully updated role for {selected_employee.split('(')[0].strip()}")
            except Exception as e:
                st.error(f"Error updating role: {e}")


def transfer_employee_branch(engine, company_id):
    """Form to transfer an employee to a different branch.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown("### Transfer Employee to Another Branch")
    
    # Get employees and branches
    with engine.connect() as conn:
        employees = EmployeeModel.get_all_employees(conn, company_id)
        branches = BranchModel.get_active_branches(conn, company_id)
    
    if not employees:
        st.warning("No employees found.")
        return
    
    if not branches or len(branches) < 2:
        st.warning("You need at least two active branches to transfer employees.")
        return
    
    # Create employee options
    employee_options = {}
    for emp in employees:
        display_name = f"{emp[2]} ({emp[7]}, {emp[5]})"
        employee_options[display_name] = (emp[0], emp[9])  # (employee_id, current_branch_id)
    
    # Create branch options
    branch_options = {branch[1]: branch[0] for branch in branches}
    
    with st.form("transfer_branch_form"):
        selected_employee = st.selectbox("Select Employee", list(employee_options.keys()))
        
        # Get current branch ID for the selected employee
        current_branch_id = employee_options[selected_employee][1]
        
        # Filter out the current branch from options
        available_branches = {k: v for k, v in branch_options.items() if v != current_branch_id}
        
        if not available_branches:
            st.warning("No other branches available for transfer.")
            st.form_submit_button("Transfer", disabled=True)
        else:
            selected_branch = st.selectbox("Transfer to Branch", list(available_branches.keys()))
            
            submitted = st.form_submit_button("Transfer")
            if submitted:
                employee_id = employee_options[selected_employee][0]
                new_branch_id = available_branches[selected_branch]
                
                # Transfer the employee
                try:
                    with engine.connect() as conn:
                        EmployeeModel.update_employee_branch(conn, employee_id, new_branch_id)
                    st.success(f"Successfully transferred {selected_employee.split('(')[0].strip()} to {selected_branch}")
                except Exception as e:
                    st.error(f"Error transferring employee: {e}")


def edit_employee(engine, company_id):
    """Edit an employee's profile.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown('<h3 class="sub-header">Edit Employee</h3>', unsafe_allow_html=True)
    
    with st.form("edit_employee_form"):
        # Display current information
        employee_id = st.session_state.edit_employee['id']
        username = st.session_state.edit_employee['username']
        st.write(f"Username: {username} (cannot be changed)")
        
        # Editable fields
        full_name = st.text_input("Full Name", value=st.session_state.edit_employee['full_name'])
        profile_pic_url = st.text_input("Profile Picture URL", 
                                       value=st.session_state.edit_employee['profile_pic_url'] or "")
        
        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("Update Profile")
        with col2:
            canceled = st.form_submit_button("Cancel")
        
        if submitted:
            if not full_name:
                st.error("Full name is required")
            else:
                # Update profile
                try:
                    with engine.connect() as conn:
                        EmployeeModel.update_profile(conn, employee_id, full_name, profile_pic_url)
                    st.success(f"Profile updated successfully for {full_name}")
                    del st.session_state.edit_employee
                    st.rerun()
                except Exception as e:
                    st.error(f"Error updating profile: {e}")
        
        if canceled:
            del st.session_state.edit_employee
            st.rerun()


#########################################
# COMPANY - MESSAGES
#########################################

def view_company_messages(engine):
    """View and send messages between company and admin.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Admin Messages</h2>', unsafe_allow_html=True)
    
    company_id = st.session_state.user["id"]
    
    # Create two columns for the layout
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Display message history
        display_message_history(engine, company_id)
    
    with col2:
        # Form to send a new message
        send_message_form(engine, company_id)


def display_message_history(engine, company_id):
    """Display message history between company and admin.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.subheader("Message History")
    
    # Fetch all messages for this company
    with engine.connect() as conn:
        messages = MessageModel.get_messages_for_company(conn, company_id)
    
    if not messages:
        st.info("No messages yet. Send a message to get started.")
    else:
        # Mark unread messages as read
        for message in messages:
            message_id = message[0]
            is_from_admin = message[1] == "admin"
            is_read = message[4]
            
            # Mark admin messages as read when viewed
            if is_from_admin and not is_read:
                with engine.connect() as conn:
                    MessageModel.mark_as_read(conn, message_id)
        
        # Display messages in a chat-like format
        for message in messages:
            message_text = message[3]
            created_at = message[5].strftime('%d %b, %Y - %H:%M') if message[5] else "Unknown"
            sender_name = message[6]  # This will be "Admin" or company name
            is_from_admin = message[1] == "admin"
            
            # Align messages based on sender (left for admin, right for company)
            alignment = "left" if is_from_admin else "right"
            bg_color = "#f1f7fe" if is_from_admin else "#e9ffe9"
            border_color = "#1E88E5" if is_from_admin else "#4CAF50"
            
            st.markdown(f'''
            <div style="display: flex; justify-content: {alignment}; margin-bottom: 10px;">
                <div style="max-width: 80%; background-color: {bg_color}; 
                            padding: 10px; border-radius: 8px; border-left: 4px solid {border_color};">
                    <div style="font-weight: 600;">{sender_name}</div>
                    <p style="margin: 5px 0;">{message_text}</p>
                    <div style="text-align: right; font-size: 0.8rem; color: #777;">{created_at}</div>
                </div>
            </div>
            ''', unsafe_allow_html=True)


def send_message_form(engine, company_id):
    """Form to send a new message to admin.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.subheader("Send Message")
    
    with st.form("send_message_form"):
        message_text = st.text_area("Message to Admin", height=150)
        
        submitted = st.form_submit_button("Send Message")
        
        if submitted:
            if not message_text:
                st.error("Please enter a message")
            else:
                try:
                    with engine.connect() as conn:
                        MessageModel.send_message(
                            conn,
                            sender_type="company",
                            sender_id=company_id,
                            receiver_type="admin",
                            receiver_id=0,  # Admin ID is 0
                            message_text=message_text
                        )
                    st.success("Message sent to Admin")
                    st.rerun()  # Refresh to show the new message
                except Exception as e:
                    st.error(f"Error sending message: {e}")


#########################################
# COMPANY - PROFILE
#########################################

def edit_company_profile(engine):
    """Edit company profile information.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Company Profile</h2>', unsafe_allow_html=True)
    
    company_id = st.session_state.user["id"]
    
    # Fetch current company data
    with engine.connect() as conn:
        company_data = CompanyModel.get_company_by_id(conn, company_id)
    
    if not company_data:
        st.error("Could not retrieve company information. Please try again later.")
        return
    
    company_name, username, profile_pic_url, is_active = company_data
    
    # Display current profile picture
    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("<p>Company Logo:</p>", unsafe_allow_html=True)
        try:
            st.image(profile_pic_url, width=150, use_container_width=False)
        except:
            st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", width=150, use_container_width=False)
    
    with col2:
        st.markdown(f"<p><strong>Company Name:</strong> {company_name}</p>", unsafe_allow_html=True)
        st.markdown(f"<p><strong>Username:</strong> {username}</p>", unsafe_allow_html=True)
        st.markdown(f"<p><strong>Status:</strong> {'Active' if is_active else 'Inactive'}</p>", unsafe_allow_html=True)
    
    # Form for updating profile
    with st.form("update_company_profile_form"):
        st.subheader("Update Company Information")
        
        # Company name update
        new_company_name = st.text_input("Company Name", value=company_name)
        
        # Profile picture URL update
        new_profile_pic_url = st.text_input("Logo/Profile URL", value=profile_pic_url or "")
        
        # Password update section
        st.subheader("Change Password")
        current_password = st.text_input("Current Password", type="password")
        new_password = st.text_input("New Password", type="password")
        confirm_password = st.text_input("Confirm New Password", type="password")
        
        submitted = st.form_submit_button("Update Profile")
        if submitted:
            updates_made = False
            
            # Check if any changes were made to name or picture URL
            if new_company_name != company_name or new_profile_pic_url != profile_pic_url:
                with engine.connect() as conn:
                    CompanyModel.update_profile(conn, company_id, new_company_name, new_profile_pic_url)
                
                # Update session state with new values
                st.session_state.user["full_name"] = new_company_name
                st.session_state.user["profile_pic_url"] = new_profile_pic_url
                
                updates_made = True
                st.success("Company information updated successfully.")
            
            # Handle password change if attempted
            if current_password or new_password or confirm_password:
                if not current_password:
                    st.error("Please enter your current password to change it.")
                elif not new_password:
                    st.error("Please enter a new password.")
                elif new_password != confirm_password:
                    st.error("New passwords do not match.")
                else:
                    # Verify current password
                    with engine.connect() as conn:
                        is_valid = CompanyModel.verify_password(conn, company_id, current_password)
                    
                    if not is_valid:
                        st.error("Current password is incorrect.")
                    else:
                        # Update password
                        with engine.connect() as conn:
                            CompanyModel.reset_password(conn, company_id, new_password)
                        
                        updates_made = True
                        st.success("Password updated successfully.")
            
            if updates_made:
                time.sleep(1)  # Give the user time to read the success message
                st.rerun()


#########################################
# COMPANY - TASKS
#########################################

def manage_company_tasks(engine):
    """View and manage all employee tasks.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Manage Tasks</h2>', unsafe_allow_html=True)
    
    tabs = st.tabs(["Task List", "Assign New Task", "Task Progress"])
    
    with tabs[0]:
        view_company_tasks(engine)
    
    with tabs[1]:
        assign_company_task(engine)
        
    with tabs[2]:
        view_task_progress(engine)


def view_company_tasks(engine):
    """View all tasks for the company with filters.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown("### All Tasks")
    
    company_id = st.session_state.user["id"]
    
    # Filters
    col1, col2 = st.columns(2)
    
    with col1:
        status_options = ["All Tasks", "Pending", "Completed"]
        status_filter = st.selectbox("Status", status_options, key="task_status_filter")
    
    with col2:
        assignment_options = ["All Assignments", "Branch Tasks", "Employee Tasks"]
        assignment_filter = st.selectbox("Assignment Type", assignment_options, key="assignment_type_filter")
    
    # Fetch tasks based on filters
    status = None if status_filter == "All Tasks" else (status_filter == "Completed")
    
    with engine.connect() as conn:
        tasks = TaskModel.get_tasks_for_company(conn, company_id, status_filter)
    
    if not tasks:
        st.info("No tasks found matching the selected criteria.")
        return
    
    # Filter by assignment type
    if assignment_filter == "Branch Tasks":
        tasks = [t for t in tasks if t[8] == "branch"]
    elif assignment_filter == "Employee Tasks":
        tasks = [t for t in tasks if t[8] == "employee"]
    
    st.write(f"Found {len(tasks)} tasks")
    
    # Display tasks
    for task in tasks:
        task_id = task[0]
        description = task[1]
        due_date = task[2].strftime('%d %b, %Y') if task[2] else "No due date"
        is_completed = task[3]
        completed_at = task[4].strftime('%d %b, %Y %H:%M') if task[4] else None
        assignee_name = task[8]
        assignee_type = task[9]
        completed_by = task[10]
        
        # Create card with appropriate styling
        bg_color = "#f0f0f0" if is_completed else "#f1fff1"
        border_color = "#9e9e9e" if is_completed else "#4CAF50"
        
        st.markdown(f'''
        <div style="background-color: {bg_color}; padding: 1rem; border-radius: 8px; 
                    margin-bottom: 0.5rem; border-left: 4px solid {border_color};">
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                <span style="font-weight: 600;">{assignee_name} ({assignee_type.capitalize()})</span>
                <span style="color: #777;">Due: {due_date}</span>
            </div>
            <p>{description}</p>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: #777; font-size: 0.8rem;">
                    {f"Completed by {completed_by} on {completed_at}" if is_completed else "Pending"}
                </span>
                <span style="font-weight: 600; color: {'#9e9e9e' if is_completed else '#4CAF50'};">
                    {"Completed" if is_completed else "Pending"}
                </span>
            </div>
        </div>
        ''', unsafe_allow_html=True)
        
        # Action buttons
        col1, col2 = st.columns(2)
        
        with col1:
            if not is_completed:
                pass  # Companies don't mark tasks as completed directly
            else:
                if st.button(f"Reopen Task", key=f"reopen_{task_id}"):
                    with engine.connect() as conn:
                        TaskModel.reopen_task(conn, task_id)
                    st.success("Task reopened")
                    st.rerun()
        
        with col2:
            if st.button(f"View Progress", key=f"progress_{task_id}"):
                st.session_state.view_task_progress = task_id
                st.rerun()
            
            # For completed tasks, offer delete option
            if is_completed:
                if st.button(f"Delete Task", key=f"delete_{task_id}"):
                    with engine.connect() as conn:
                        TaskModel.delete_task(conn, task_id)
                    st.success("Task deleted")
                    st.rerun()
    
    # Show task progress if selected
    if hasattr(st.session_state, 'view_task_progress'):
        display_task_progress(engine, st.session_state.view_task_progress)


def display_task_progress(engine, task_id):
    """Display progress details for a branch-level task.
    
    Args:
        engine: SQLAlchemy database engine
        task_id: ID of the task
    """
    st.markdown("### Task Progress Details")
    
    with engine.connect() as conn:
        progress = TaskModel.get_branch_task_progress(conn, task_id)
    
    if not progress:
        st.info("This is not a branch-level task or no progress data is available.")
        
        # Close button
        if st.button("Close Progress View"):
            del st.session_state.view_task_progress
            st.rerun()
        
        return
    
    # Display progress stats
    total = progress['total']
    completed = progress['completed']
    completion_percentage = round((completed / total) * 100) if total > 0 else 0
    
    st.write(f"**Completion Rate:** {completed}/{total} employees ({completion_percentage}%)")
    
    # Progress bar
    st.progress(completion_percentage / 100)
    
    # Group statuses by role
    statuses_by_role = {}
    for status in progress['employee_statuses']:
        employee_id = status[0]
        name = status[1]
        is_completed = status[2]
        role = status[3]
        
        if role not in statuses_by_role:
            statuses_by_role[role] = []
        
        statuses_by_role[role].append((name, is_completed))
    
    # Display employee completion status by role
    for role, employees in sorted(statuses_by_role.items()):
        st.markdown(f"**{role}s:**")
        
        for name, is_completed in employees:
            icon = "✅" if is_completed else "⏳"
            st.write(f"{icon} {name}")
    
    # Close button
    if st.button("Close Progress View"):
        del st.session_state.view_task_progress
        st.rerun()


def assign_company_task(engine):
    """Form to assign a new task to a branch or employee.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown("### Assign New Task")
    
    company_id = st.session_state.user["id"]
    
    # Get active branches and employees
    with engine.connect() as conn:
        branches = BranchModel.get_active_branches(conn, company_id)
        roles = RoleModel.get_all_roles(conn, company_id)
    
    if not branches:
        st.warning("No active branches found. Please add and activate branches first.")
        return
    
    # Assignment options
    assignment_options = ["Branch", "Individual Employee"]
    assignment_type = st.radio("Assign To", assignment_options)
    
    with st.form("assign_task_form"):
        # Task details
        task_description = st.text_area("Task Description", height=100)
        due_date = st.date_input("Due Date", value=datetime.date.today() + datetime.timedelta(days=7))
        
        # Assignment based on selected type
        if assignment_type == "Branch":
            # Branch selection
            branch_options = {branch[1]: branch[0] for branch in branches}
            selected_branch = st.selectbox("Select Branch", list(branch_options.keys()))
            branch_id = branch_options[selected_branch] if selected_branch else None
            employee_id = None
        else:
            # Employee selection - first select branch, then employee
            branch_options = {branch[1]: branch[0] for branch in branches}
            selected_branch = st.selectbox("Employee's Branch", list(branch_options.keys()))
            
            if selected_branch:
                branch_id = branch_options[selected_branch]
                
                # Get employees for this branch
                with engine.connect() as conn:
                    branch_employees = EmployeeModel.get_branch_employees(conn, branch_id)
                
                if not branch_employees:
                    st.warning(f"No employees found in {selected_branch}.")
                    employee_id = None
                else:
                    # Group employees by role
                    employees_by_role = {}
                    for emp in branch_employees:
                        role_name = emp[5]
                        if role_name not in employees_by_role:
                            employees_by_role[role_name] = []
                        
                        employees_by_role[role_name].append((emp[0], emp[2]))  # (id, name)
                    
                    # Create a formatted selection list
                    employee_options = {}
                    for role_name, employees in sorted(employees_by_role.items()):
                        for emp_id, emp_name in employees:
                            employee_options[f"{emp_name} ({role_name})"] = emp_id
                    
                    selected_employee = st.selectbox("Select Employee", list(employee_options.keys()))
                    employee_id = employee_options[selected_employee] if selected_employee else None
                    branch_id = None  # Set to None since we're assigning directly to employee
            else:
                employee_id = None
        
        submitted = st.form_submit_button("Assign Task")
        if submitted:
            if not task_description:
                st.error("Please enter a task description")
            elif assignment_type == "Branch" and not branch_id:
                st.error("Please select a branch")
            elif assignment_type == "Individual Employee" and not employee_id:
                st.error("Please select an employee")
            else:
                # Create the task
                try:
                    with engine.connect() as conn:
                        task_id = TaskModel.create_task(
                            conn,
                            company_id, 
                            task_description, 
                            due_date,
                            branch_id,
                            employee_id
                        )
                    
                    if branch_id:
                        st.success(f"Task assigned to branch: {selected_branch}")
                    else:
                        st.success(f"Task assigned to employee: {selected_employee.split('(')[0].strip()}")
                except Exception as e:
                    st.error(f"Error assigning task: {e}")


def view_task_progress(engine):
    """View progress of branch-level tasks.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown("### Branch Task Progress")
    
    company_id = st.session_state.user["id"]
    
    # Get all branch-level tasks
    with engine.connect() as conn:
        tasks = TaskModel.get_tasks_for_company(conn, company_id)
    
    # Filter to only branch tasks
    branch_tasks = [t for t in tasks if t[9] == "branch"]
    
    if not branch_tasks:
        st.info("No branch-level tasks found.")
        return
    
    # Group tasks by status
    pending_tasks = [t for t in branch_tasks if not t[3]]
    completed_tasks = [t for t in branch_tasks if t[3]]
    
    # Display pending tasks first
    if pending_tasks:
        st.markdown("#### Pending Branch Tasks")
        
        for task in pending_tasks:
            task_id = task[0]
            description = task[1]
            due_date = task[2].strftime('%d %b, %Y') if task[2] else "No due date"
            branch_name = task[8]
            
            with st.expander(f"{branch_name}: {description[:50]}{'...' if len(description) > 50 else ''}", expanded=False):
                st.write(f"**Due Date:** {due_date}")
                st.write(f"**Description:** {description}")
                
                # Show progress
                with engine.connect() as conn:
                    progress = TaskModel.get_branch_task_progress(conn, task_id)
                
                if progress:
                    total = progress['total']
                    completed = progress['completed']
                    completion_percentage = round((completed / total) * 100) if total > 0 else 0
                    
                    st.write(f"**Completion Rate:** {completed}/{total} employees ({completion_percentage}%)")
                    st.progress(completion_percentage / 100)
                    
                    # Group by role for more compact display
                    completed_by_role = {}
                    total_by_role = {}
                    
                    for status in progress['employee_statuses']:
                        role = status[3]
                        is_completed = status[2]
                        
                        if role not in completed_by_role:
                            completed_by_role[role] = 0
                            total_by_role[role] = 0
                        
                        total_by_role[role] += 1
                        if is_completed:
                            completed_by_role[role] += 1
                    
                    # Display by role
                    for role in total_by_role.keys():
                        role_percentage = round((completed_by_role[role] / total_by_role[role]) * 100)
                        st.write(f"**{role}s:** {completed_by_role[role]}/{total_by_role[role]} ({role_percentage}%)")
    
    # Display completed tasks
    if completed_tasks:
        st.markdown("#### Completed Branch Tasks")
        
        for task in completed_tasks:
            task_id = task[0]
            description = task[1]
            completed_at = task[4].strftime('%d %b, %Y %H:%M') if task[4] else "Unknown"
            branch_name = task[8]
            completed_by = task[10]
            
            st.markdown(f'''
            <div style="background-color: #f0f0f0; padding: 1rem; border-radius: 8px; 
                        margin-bottom: 0.5rem; border-left: 4px solid #9e9e9e;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span style="font-weight: 600;">{branch_name}</span>
                    <span style="color: #777;">Completed: {completed_at}</span>
                </div>
                <p>{description}</p>
                <div style="text-align: right; color: #777; font-size: 0.8rem;">
                    Marked complete by: {completed_by if completed_by else "All employees"}
                </div>
            </div>
            ''', unsafe_allow_html=True)


#########################################
# COMPANY - REPORTS
#########################################

def manage_reports(engine):
    """View and download reports with various filters.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Reports</h2>', unsafe_allow_html=True)
    
    company_id = st.session_state.user["id"]
    company_name = st.session_state.user["full_name"]
    
    tabs = st.tabs(["All Reports", "Branch Reports", "Role Reports", "Employee Reports"])
    
    with tabs[0]:
        view_company_reports(engine, company_id, company_name)
    
    with tabs[1]:
        view_branch_reports(engine, company_id, company_name)
        
    with tabs[2]:
        view_role_reports(engine, company_id, company_name)
        
    with tabs[3]:
        view_employee_reports(engine, company_id)


def view_company_reports(engine, company_id, company_name):
    """View and download reports for the entire company.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
        company_name: Name of the company for display
    """
    st.markdown("### Company-wide Reports")
    
    # Date range filter
    col1, col2 = st.columns(2)
    
    with col1:
        date_options = [
            "This Week",
            "This Month",
            "This Year",
            "All Reports",
            "Custom Range"
        ]
        date_filter = st.selectbox("Date Range", date_options, key="company_reports_date_filter")
    
    with col2:
        # Custom date range if selected
        if date_filter == "Custom Range":
            today = datetime.date.today()
            start_date = st.date_input("Start Date", today - datetime.timedelta(days=30))
            end_date = st.date_input("End Date", today)
        else:
            # Set default dates based on filter
            start_date, end_date = get_date_range_from_filter(date_filter)
    
    # Fetch reports
    with engine.connect() as conn:
        reports = ReportModel.get_company_reports(conn, company_id, start_date, end_date)
    
    if not reports:
        st.info("No reports found for the selected period.")
        return
    
    # Display report stats
    total_reports = len(reports)
    unique_employees = len(set(r[1] for r in reports))  # Unique employee names
    unique_branches = len(set(r[3] for r in reports))  # Unique branch names
    
    st.write(f"Found {total_reports} reports from {unique_employees} employees across {unique_branches} branches.")
    
    # Download button
    if st.button("Download as PDF", key="download_company_reports"):
        pdf = create_company_report_pdf(reports, company_name)
        
        # Format date range for filename
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        st.download_button(
            label="Download PDF",
            data=pdf,
            file_name=f"{company_name}_reports_{start_str}_to_{end_str}.pdf",
            mime="application/pdf"
        )
    
    # Display reports grouped by branch and employee
    reports_by_branch = {}
    for report in reports:
        branch_name = report[3]
        
        if branch_name not in reports_by_branch:
            reports_by_branch[branch_name] = {}
        
        employee_name = report[1]
        if employee_name not in reports_by_branch[branch_name]:
            reports_by_branch[branch_name][employee_name] = []
        
        reports_by_branch[branch_name][employee_name].append(report)
    
    # Display branches
    for branch_name, employees in reports_by_branch.items():
        with st.expander(f"Branch: {branch_name} ({sum(len(reports) for reports in employees.values())} reports)", expanded=False):
            # Display employees in this branch
            for employee_name, emp_reports in employees.items():
                with st.expander(f"{employee_name} ({len(emp_reports)} reports)", expanded=False):
                    # Group by date
                    emp_reports_by_date = {}
                    for report in emp_reports:
                        date = report[4]
                        if date not in emp_reports_by_date:
                            emp_reports_by_date[date] = report
                    
                    # Display each date
                    for date, report in sorted(emp_reports_by_date.items(), key=lambda x: x[0], reverse=True):
                        report_text = report[5]
                        
                        st.markdown(f'''
                        <div class="report-item">
                            <strong>{date.strftime('%A, %d %b %Y')}</strong>
                            <p>{report_text}</p>
                        </div>
                        ''', unsafe_allow_html=True)


def view_branch_reports(engine, company_id, company_name):
    """View and download reports for a specific branch.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
        company_name: Name of the company for display
    """
    st.markdown("### Branch Reports")
    
    # Get active branches
    with engine.connect() as conn:
        branches = BranchModel.get_active_branches(conn, company_id)
    
    if not branches:
        st.warning("No active branches found.")
        return
    
    # Branch selection
    branch_options = {branch[1]: branch[0] for branch in branches}
    selected_branch = st.selectbox("Select Branch", list(branch_options.keys()))
    branch_id = branch_options[selected_branch]
    
    # Date range filter
    col1, col2 = st.columns(2)
    
    with col1:
        date_options = [
            "This Week",
            "This Month",
            "This Year",
            "All Reports",
            "Custom Range"
        ]
        date_filter = st.selectbox("Date Range", date_options, key="branch_reports_date_filter")
    
    with col2:
        # Custom date range if selected
        if date_filter == "Custom Range":
            today = datetime.date.today()
            start_date = st.date_input("Start Date", today - datetime.timedelta(days=30), key="branch_start_date")
            end_date = st.date_input("End Date", today, key="branch_end_date")
        else:
            # Set default dates based on filter
            start_date, end_date = get_date_range_from_filter(date_filter)
    
    # Fetch reports
    with engine.connect() as conn:
        reports = ReportModel.get_branch_reports(conn, branch_id, start_date, end_date)
    
    if not reports:
        st.info("No reports found for the selected branch and period.")
        return
    
    # Display report stats
    total_reports = len(reports)
    unique_employees = len(set(r[1] for r in reports))  # Unique employee names
    
    st.write(f"Found {total_reports} reports from {unique_employees} employees in {selected_branch}.")
    
    # Download button
    if st.button("Download as PDF", key="download_branch_reports"):
        pdf = create_branch_report_pdf(reports, selected_branch)
        
        # Format date range for filename
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        st.download_button(
            label="Download PDF",
            data=pdf,
            file_name=f"{selected_branch}_reports_{start_str}_to_{end_str}.pdf",
            mime="application/pdf"
        )
    
    # Display reports grouped by employee
    reports_by_employee = {}
    for report in reports:
        employee_name = report[1]
        role_name = report[2]
        key = f"{employee_name} ({role_name})"
        
        if key not in reports_by_employee:
            reports_by_employee[key] = []
        
        reports_by_employee[key].append(report)
    
    # Display employees
    for employee, emp_reports in reports_by_employee.items():
        with st.expander(f"{employee} ({len(emp_reports)} reports)", expanded=False):
            # Display each report
            for report in sorted(emp_reports, key=lambda x: x[3], reverse=True):
                report_date = report[3]
                report_text = report[4]
                
                st.markdown(f'''
                <div class="report-item">
                    <strong>{report_date.strftime('%A, %d %b %Y')}</strong>
                    <p>{report_text}</p>
                </div>
                ''', unsafe_allow_html=True)


def view_role_reports(engine, company_id, company_name):
    """View and download reports for a specific role.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
        company_name: Name of the company for display
    """
    st.markdown("### Role-based Reports")
    
    # Get roles
    with engine.connect() as conn:
        roles = RoleModel.get_all_roles(conn, company_id)
    
    if not roles:
        st.warning("No roles found.")
        return
    
    # Role selection
    role_options = {role[1]: role[0] for role in roles}
    selected_role = st.selectbox("Select Role", list(role_options.keys()))
    role_id = role_options[selected_role]
    
    # Date range filter
    col1, col2 = st.columns(2)
    
    with col1:
        date_options = [
            "This Week",
            "This Month",
            "This Year",
            "All Reports",
            "Custom Range"
        ]
        date_filter = st.selectbox("Date Range", date_options, key="role_reports_date_filter")
    
    with col2:
        # Custom date range if selected
        if date_filter == "Custom Range":
            today = datetime.date.today()
            start_date = st.date_input("Start Date", today - datetime.timedelta(days=30), key="role_start_date")
            end_date = st.date_input("End Date", today, key="role_end_date")
        else:
            # Set default dates based on filter
            start_date, end_date = get_date_range_from_filter(date_filter)
    
    # Fetch reports
    with engine.connect() as conn:
        reports = ReportModel.get_company_reports(conn, company_id, start_date, end_date, role_id=role_id)
    
    if not reports:
        st.info(f"No reports found for {selected_role}s in the selected period.")
        return
    
    # Display report stats
    total_reports = len(reports)
    unique_employees = len(set(r[1] for r in reports))  # Unique employee names
    unique_branches = len(set(r[3] for r in reports))  # Unique branch names
    
    st.write(f"Found {total_reports} reports from {unique_employees} {selected_role}s across {unique_branches} branches.")
    
    # Download button
    if st.button("Download as PDF", key="download_role_reports"):
        pdf = create_role_report_pdf(reports, selected_role, company_name)
        
        # Format date range for filename
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        st.download_button(
            label="Download PDF",
            data=pdf,
            file_name=f"{selected_role}_reports_{start_str}_to_{end_str}.pdf",
            mime="application/pdf"
        )
    
    # Display reports grouped by branch and employee
    reports_by_branch = {}
    for report in reports:
        branch_name = report[3]
        
        if branch_name not in reports_by_branch:
            reports_by_branch[branch_name] = {}
        
        employee_name = report[1]
        if employee_name not in reports_by_branch[branch_name]:
            reports_by_branch[branch_name][employee_name] = []
        
        reports_by_branch[branch_name][employee_name].append(report)
    
    # Display branches
    for branch_name, employees in reports_by_branch.items():
        with st.expander(f"Branch: {branch_name} ({sum(len(reports) for reports in employees.values())} reports)", expanded=False):
            # Display employees in this branch
            for employee_name, emp_reports in employees.items():
                with st.expander(f"{employee_name} ({len(emp_reports)} reports)", expanded=False):
                    # Display each report
                    for report in sorted(emp_reports, key=lambda x: x[4], reverse=True):
                        report_date = report[4]
                        report_text = report[5]
                        
                        st.markdown(f'''
                        <div class="report-item">
                            <strong>{report_date.strftime('%A, %d %b %Y')}</strong>
                            <p>{report_text}</p>
                        </div>
                        ''', unsafe_allow_html=True)


def view_employee_reports(engine, company_id):
    """View and download reports for a specific employee.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the current company
    """
    st.markdown("### Individual Employee Reports")
    assignment_optionsdef display_report_item(date_str, text, author=None):
    """Display a report item with consistent styling.
    
    Args:
        date_str: Formatted date string
        text: Report text content
        author: (Optional) Author name for admin view
    """
    header = f"<strong>{author}</strong> - {date_str}" if author else f"<strong>{date_str}</strong>"
    
    st.markdown(f'''
    <div class="report-item">
        {header}
        <p>{text[:100]}{'...' if len(text) > 100 else ''}</p>
    </div>
    ''', unsafe_allow_html=True)


def display_task_item(description, due_date, is_completed=False, author=None):
    """Display a task item with consistent styling.
    
    Args:
        description: Task description
        due_date: Formatted due date string
        is_completed: Boolean indicating if task is completed
        author: (Optional) Author name for admin view
    """
    status_class = "completed" if is_completed else ""
    header = f"<strong>{author}</strong> - Due: {due_date}" if author else f"<strong>Due: {due_date}</strong>"
    
    st.markdown(f'''
    <div class="task-item {status_class}">
        {header}
        <p>{description[:100]}{'...' if len(description) > 100 else ''}</p>
    </div>
    ''', unsafe_allow_html=True)


#########################################
# LOGIN PAGE
#########################################

def display_login(engine):
    """Display the login page.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown('<div class="login-header">', unsafe_allow_html=True)
    st.markdown('<h1 class="main-header">Employee Management System</h1>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    username = st.text_input("Username", key="login_username")
    password = st.text_input("Password", type="password", key="login_password")
    
    if st.button("Login"):
        user = authenticate(engine, username, password)
        if user:
            st.session_state.user = user
            st.rerun()
        else:
            st.error("Invalid username or password")
    
    st.markdown('</div>', unsafe_allow_html=True)


#########################################
# ADMIN DASHBOARD
#########################################

def admin_dashboard(engine):
    """Display the admin dashboard.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h1 class="main-header">Admin Dashboard</h1>', unsafe_allow_html=True)
    
    # Display admin profile
    display_profile_header(st.session_state.user)
    
    # Navigation
    selected = admin_navigation()
    
    if selected == "Dashboard":
        display_admin_dashboard_overview(engine)
    elif selected == "Companies":
        manage_companies(engine)
    elif selected == "Messages":
        manage_messages(engine)
    elif selected == "Employees":
        manage_employees(engine)
    elif selected == "Reports":
        view_all_reports(engine)
    elif selected == "Tasks":
        manage_tasks(engine)
    elif selected == "Logout":
        logout()


def admin_navigation():
    """Create and return the admin navigation menu with new options.
    
    Returns:
        str: Selected menu option
    """
    return st.sidebar.radio(
        "Navigation",
        ["Dashboard", "Companies", "Messages", "Employees", "Reports", "Tasks", "Logout"],
        index=0
    )


def display_admin_dashboard_overview(engine):
    """Display the admin dashboard overview with statistics and recent activities.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Overview</h2>', unsafe_allow_html=True)
    
    # Statistics
    with engine.connect() as conn:
        # Total companies
        result = conn.execute(text('SELECT COUNT(*) FROM companies WHERE is_active = TRUE'))
        total_companies = result.fetchone()[0]
        
        # Total branches
        result = conn.execute(text('SELECT COUNT(*) FROM branches WHERE is_active = TRUE'))
        total_branches = result.fetchone()[0]
        
        # Total employees
        result = conn.execute(text('SELECT COUNT(*) FROM employees WHERE is_active = TRUE'))
        total_employees = result.fetchone()[0]
        
        # Total reports
        result = conn.execute(text('SELECT COUNT(*) FROM daily_reports'))
        total_reports = result.fetchone()[0]
        
        # Total tasks
        result = conn.execute(text('SELECT COUNT(*) FROM tasks'))
        total_tasks = result.fetchone()[0]
        
        # Completed tasks
        result = conn.execute(text('SELECT COUNT(*) FROM tasks WHERE is_completed = TRUE'))
        completed_tasks = result.fetchone()[0]
        
        # Unread messages
        result = conn.execute(text('''
        SELECT COUNT(*) FROM messages 
        WHERE receiver_type = 'admin' AND is_read = FALSE
        '''))
        unread_messages = result.fetchone()[0]
        
        # Recent company additions
        result = conn.execute(text('''
        SELECT company_name, created_at 
        FROM companies 
        ORDER BY created_at DESC 
        LIMIT 5
        '''))
        recent_companies = result.fetchall()
        
        # Recent messages
        result = conn.execute(text('''
        SELECT m.message_text, m.created_at, 
               CASE WHEN m.sender_type = 'company' THEN c.company_name ELSE 'Admin' END as sender_name
        FROM messages m
        LEFT JOIN companies c ON m.sender_type = 'company' AND m.sender_id = c.id
        WHERE m.receiver_type = 'admin'
        ORDER BY m.created_at DESC 
        LIMIT 5
        '''))
        recent_messages = result.fetchall()
    
    # Display statistics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        display_stats_card(total_companies, "Active Companies")
    
    with col2:
        display_stats_card(total_branches, "Active Branches")
    
    with col3:
        display_stats_card(total_employees, "Active Employees")
    
    with col4:
        display_stats_card(unread_messages, "Unread Messages")
    
    # Second row of stats
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        display_stats_card(total_reports, "Total Reports")
    
    with col2:
        display_stats_card(total_tasks, "Total Tasks")
    
    with col3:
        completion_rate = calculate_completion_rate(total_tasks, completed_tasks)
        display_stats_card(f"{completion_rate}%", "Task Completion")
    
    # Recent activities
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown('<h3 class="sub-header">Recent Companies</h3>', unsafe_allow_html=True)
        if recent_companies:
            for company in recent_companies:
                company_name = company[0]
                created_at = company[1].strftime('%d %b, %Y') if company[1] else "Unknown"
                
                st.markdown(f'''
                <div class="card">
                    <strong>{company_name}</strong>
                    <p style="color: #777; font-size: 0.8rem;">Added on {created_at}</p>
                </div>
                ''', unsafe_allow_html=True)
        else:
            st.info("No companies added yet")
    
    with col2:
        st.markdown('<h3 class="sub-header">Recent Messages</h3>', unsafe_allow_html=True)
        if recent_messages:
            for message in recent_messages:
                message_text = message[0]
                created_at = message[1].strftime('%d %b, %Y') if message[1] else "Unknown"
                sender_name = message[2]
                
                st.markdown(f'''
                <div class="report-item">
                    <span style="font-weight: 600;">{sender_name}</span> - <span style="color: #777;">{created_at}</span>
                    <p>{message_text[:100]}{'...' if len(message_text) > 100 else ''}</p>
                </div>
                ''', unsafe_allow_html=True)
        else:
            st.info("No messages available")


#########################################
# ADMIN - COMPANIES
#########################################

def manage_companies(engine):
    """Manage companies - listing, adding, activating/deactivating.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Manage Companies</h2>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Company List", "Add New Company"])
    
    with tab1:
        display_company_list(engine)
    
    with tab2:
        add_new_company(engine)


def display_company_list(engine):
    """Display the list of companies with management options.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Fetch and display all companies
    with engine.connect() as conn:
        companies = CompanyModel.get_all_companies(conn)
    
    if not companies:
        st.info("No companies found. Add companies using the 'Add New Company' tab.")
    else:
        st.write(f"Total companies: {len(companies)}")
        
        for company in companies:
            company_id = company[0]
            company_name = company[1]
            username = company[2]
            profile_pic_url = company[3]
            is_active = company[4]
            created_at = company[5].strftime('%d %b, %Y') if company[5] else "Unknown"
            
            with st.expander(f"{company_name} (Username: {username})", expanded=False):
                col1, col2 = st.columns([1, 3])
                
                with col1:
                    try:
                        st.image(profile_pic_url, width=100, use_container_width=False)
                    except:
                        st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", width=100)
                
                with col2:
                    st.write(f"**Company:** {company_name}")
                    st.write(f"**Username:** {username}")
                    st.write(f"**Status:** {'Active' if is_active else 'Inactive'}")
                    st.write(f"**Created:** {created_at}")
                    
                    # Action buttons
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if is_active:  # If active
                            if st.button(f"Deactivate", key=f"deactivate_company_{company_id}"):
                                with engine.connect() as conn:
                                    CompanyModel.update_company_status(conn, company_id, False)
                                st.success(f"Deactivated company: {company_name}")
                                st.rerun()
                        else:  # If inactive
                            if st.button(f"Activate", key=f"activate_company_{company_id}"):
                                with engine.connect() as conn:
                                    CompanyModel.update_company_status(conn, company_id, True)
                                st.success(f"Activated company: {company_name}")
                                st.rerun()
                    
                    with col2:
                        if st.button(f"Reset Password", key=f"reset_company_{company_id}"):
                            new_password = "company123"  # Default reset password
                            with engine.connect() as conn:
                                CompanyModel.reset_password(conn, company_id, new_password)
                            st.success(f"Password reset to '{new_password}' for {company_name}")
                    
                    with col3:
                        if st.button(f"View Branches", key=f"view_branches_{company_id}"):
                            st.session_state.view_company_branches = company_id
                            st.session_state.view_company_name = company_name
                            st.rerun()
                            
                # Display branches if requested
                if hasattr(st.session_state, 'view_company_branches') and st.session_state.view_company_branches == company_id:
                    display_company_branches(engine, company_id, st.session_state.view_company_name)


def display_company_branches(engine, company_id, company_name):
    """Display branches for a specific company.
    
    Args:
        engine: SQLAlchemy database engine
        company_id: ID of the company
        company_name: Name of the company for display
    """
    st.markdown(f'<h3 class="sub-header">Branches for {company_name}</h3>', unsafe_allow_html=True)
    
    # Fetch branches for this company
    with engine.connect() as conn:
        branches = BranchModel.get_company_branches(conn, company_id)
    
    if not branches:
        st.info(f"No branches found for {company_name}.")
    else:
        st.write(f"Total branches: {len(branches)}")
        
        for branch in branches:
            branch_id = branch[0]
            branch_name = branch[1]
            location = branch[2] or "N/A"
            branch_head = branch[3] or "N/A"
            is_active = branch[4]
            
            st.markdown(f'''
            <div class="card">
                <h4>{branch_name}</h4>
                <p><strong>Location:</strong> {location}</p>
                <p><strong>Branch Head:</strong> {branch_head}</p>
                <p><strong>Status:</strong> {'Active' if is_active else 'Inactive'}</p>
            </div>
            ''', unsafe_allow_html=True)
    
    # Close button
    if st.button("Close Branches View", key=f"close_branches_{company_id}"):
        del st.session_state.view_company_branches
        del st.session_state.view_company_name
        st.rerun()


def add_new_company(engine):
    """Form to add a new company.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Form to add new company
    with st.form("add_company_form"):
        company_name = st.text_input("Company Name", help="Name of the company")
        username = st.text_input("Username", help="Username for company login")
        password = st.text_input("Password", type="password", help="Initial password")
        profile_pic_url = st.text_input("Profile Picture URL", help="Link to company logo or profile picture")
        
        submitted = st.form_submit_button("Add Company")
        if submitted:
            if not company_name or not username or not password:
                st.error("Please fill all required fields")
            else:
                # Check if company name or username already exists
                with engine.connect() as conn:
                    # Check company name
                    result = conn.execute(text('SELECT COUNT(*) FROM companies WHERE company_name = :company_name'), 
                                          {'company_name': company_name})
                    name_count = result.fetchone()[0]
                    
                    # Check username
                    result = conn.execute(text('SELECT COUNT(*) FROM companies WHERE username = :username'), 
                                          {'username': username})
                    username_count = result.fetchone()[0]
                    
                    if name_count > 0:
                        st.error(f"Company name '{company_name}' already exists")
                    elif username_count > 0:
                        st.error(f"Username '{username}' already exists")
                    else:
                        # Insert new company
                        try:
                            CompanyModel.add_company(conn, company_name, username, password, profile_pic_url)
                            st.success(f"Successfully added company: {company_name}")
                        except Exception as e:
                            st.error(f"Error adding company: {e}")


#########################################
# ADMIN - MESSAGING
#########################################

def manage_messages(engine):
    """Admin message management - send and view messages to/from companies.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Company Messages</h2>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["View Messages", "Send New Message"])
    
    with tab1:
        view_messages(engine)
    
    with tab2:
        send_message(engine)


def view_messages(engine):
    """View messages from companies.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Fetch all messages for admin
    with engine.connect() as conn:
        messages = MessageModel.get_messages_for_admin(conn)
    
    if not messages:
        st.info("No messages found.")
    else:
        st.write(f"Total messages: {len(messages)}")
        
        # Group messages by sender
        messages_by_sender = {}
        for message in messages:
            sender_name = message[6]  # sender_name from query
            if sender_name not in messages_by_sender:
                messages_by_sender[sender_name] = []
            messages_by_sender[sender_name].append(message)
        
        # Display messages by sender
        for sender_name, sender_messages in messages_by_sender.items():
            with st.expander(f"Messages from {sender_name} ({len(sender_messages)})", expanded=False):
                for message in sender_messages:
                    message_id = message[0]
                    message_text = message[3]
                    is_read = message[4]
                    created_at = message[5].strftime('%d %b, %Y - %H:%M') if message[5] else "Unknown"
                    
                    # Style based on read status
                    background_color = "#f0f0f0" if is_read else "#f1fff1"
                    border_color = "#9e9e9e" if is_read else "#4CAF50"
                    
                    st.markdown(f'''
                    <div style="background-color: {background_color}; padding: 1rem; border-radius: 8px; 
                                margin-bottom: 0.5rem; border-left: 4px solid {border_color};">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                            <span style="font-weight: 600;">{sender_name}</span>
                            <span style="color: #777;">{created_at}</span>
                        </div>
                        <p>{message_text}</p>
                    </div>
                    ''', unsafe_allow_html=True)
                    
                    # Mark as read button (if not already read)
                    if not is_read:
                        if st.button("Mark as Read", key=f"mark_read_{message_id}"):
                            with engine.connect() as conn:
                                MessageModel.mark_as_read(conn, message_id)
                            st.success("Message marked as read")
                            st.rerun()


def send_message(engine):
    """Send a message to a company.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Get active companies for recipient selection
    with engine.connect() as conn:
        companies = CompanyModel.get_active_companies(conn)
    
    if not companies:
        st.warning("No active companies found. Please add and activate companies first.")
        return
    
    # Create company selection dictionary
    company_options = {company[1]: company[0] for company in companies}
    
    # Message form
    with st.form("send_message_form"):
        st.subheader("New Message")
        
        recipient_name = st.selectbox("Select Company", list(company_options.keys()))
        message_text = st.text_area("Message", height=150)
        
        submitted = st.form_submit_button("Send Message")
        
        if submitted:
            if not message_text:
                st.error("Please enter a message")
            else:
                # Get company ID from selection
                recipient_id = company_options[recipient_name]
                
                try:
                    with engine.connect() as conn:
                        MessageModel.send_message(
                            conn,
                            sender_type="admin",
                            sender_id=0,  # Admin ID is 0
                            receiver_type="company",
                            receiver_id=recipient_id,
                            message_text=message_text
                        )
                    st.success(f"Message sent to {recipient_name}")
                except Exception as e:
                    st.error(f"Error sending message: {e}")


#########################################
# ADMIN - EMPLOYEES
#########################################

def manage_employees(engine):
    """Manage employees - listing, adding, activating/deactivating.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Manage Employees</h2>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Employee List", "Add New Employee"])
    
    with tab1:
        display_employee_list(engine)
    
    with tab2:
        add_new_employee(engine)


def display_employee_list(engine):
    """Display the list of employees with management options.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Fetch and display all employees
    with engine.connect() as conn:
        employees = EmployeeModel.get_all_employees(conn)
    
    if not employees:
        st.info("No employees found. Add employees using the 'Add New Employee' tab.")
    else:
        st.write(f"Total employees: {len(employees)}")
        
        for i, employee in enumerate(employees):
            with st.expander(f"{employee[2]} ({employee[1]})", expanded=False):
                col1, col2 = st.columns([1, 3])
                
                with col1:
                    try:
                        st.image(employee[3], width=100, use_container_width=False)
                    except:
                        st.image("https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y", width=100)
                
                with col2:
                    st.write(f"**Username:** {employee[1]}")
                    st.write(f"**Full Name:** {employee[2]}")
                    st.write(f"**Status:** {'Active' if employee[4] else 'Inactive'}")
                    
                    # Action buttons
                    col1, col2 = st.columns(2)
                    with col1:
                        if employee[4]:  # If active
                            if st.button(f"Deactivate", key=f"deactivate_{employee[0]}"):
                                with engine.connect() as conn:
                                    EmployeeModel.update_employee_status(conn, employee[0], False)
                                st.success(f"Deactivated employee: {employee[2]}")
                                st.rerun()
                        else:  # If inactive
                            if st.button(f"Activate", key=f"activate_{employee[0]}"):
                                with engine.connect() as conn:
                                    EmployeeModel.update_employee_status(conn, employee[0], True)
                                st.success(f"Activated employee: {employee[2]}")
                                st.rerun()
                    
                    with col2:
                        if st.button(f"Reset Password", key=f"reset_{employee[0]}"):
                            new_password = "password123"  # Default reset password
                            with engine.connect() as conn:
                                EmployeeModel.reset_password(conn, employee[0], new_password)
                            st.success(f"Password reset to '{new_password}' for {employee[2]}")


def add_new_employee(engine):
    """Form to add a new employee.
    
    Args:
        engine: SQLAlchemy database engine
    """
    # Form to add new employee
    with st.form("add_employee_form"):
        username = st.text_input("Username", help="Username for employee login")
        password = st.text_input("Password", type="password", help="Initial password")
        full_name = st.text_input("Full Name")
        profile_pic_url = st.text_input("Profile Picture URL", help="Link to employee profile picture")
        
        submitted = st.form_submit_button("Add Employee")
        if submitted:
            if not username or not password or not full_name:
                st.error("Please fill all required fields")
            else:
                # Check if username already exists
                with engine.connect() as conn:
                    result = conn.execute(text('SELECT COUNT(*) FROM employees WHERE username = :username'), 
                                          {'username': username})
                    count = result.fetchone()[0]
                    
                    if count > 0:
                        st.error(f"Username '{username}' already exists")
                    else:
                        # Insert new employee
                        try:
                            EmployeeModel.add_employee(conn, username, password, full_name, profile_pic_url)
                            st.success(f"Successfully added employee: {full_name}")
                        except Exception as e:
                            st.error(f"Error adding employee: {e}")


#########################################
# ADMIN - REPORTS
#########################################

def view_all_reports(engine):
    """Display and manage all employee reports.
    
    Args:
        engine: SQLAlchemy database engine
    """
    st.markdown('<h2 class="sub-header">Employee Reports</h2>', unsafe_allow_html=True)
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        # Employee filter
        with engine.connect() as conn:
            employees = EmployeeModel.get_active_employees(conn)
        
        employee_options = ["All Employees"] + [emp[1] for emp in employees]
        employee_filter = st.selectbox("Select Employee", employee_options, key="reports_employee_filter")
    
    with col2:
        # Date range filter
        date_options = [
            "All Time",
            "Today",
            "This Week",
            "This Month",
            "This Year",
            "Custom Range"
        ]
        date_filter = st.selectbox("Date Range", date_options, key="reports_date_filter")
    
    with col3:
        # Custom date range if selected
        if date_filter == "Custom Range":
            today = datetime.date.today()
            start_date = st.date_input("Start Date", today - datetime.timedelta(days=30))
            end_date = st.date_input("End Date", today)
        else:
            # Set default dates based on filter
            start_date, end_date = get_date_range_from_filter(date_filter)
    
    # Fetch reports based on filters
    with engine.connect() as conn:
        reports = ReportModel.get_all_reports(conn, start_date, end_date, employee_name=employee_filter)
    
    # Display reports
    if not reports:
        st.info("No reports found for the selected criteria")
    else:
        st.write(f"Found {len(reports)} reports")
        
        # Group by employee for export
        employee_reports = {}
        for report in reports:
            if report[0] not in employee_reports:
                employee_reports[report[0]] = []
            employee_reports[report[0]].append(report)
        
        # Export options
        col1, col2 = st.columns([3, 1])
        with col2:
            if employee_filter != "All Employees" and len(employee_reports) == 1:
                if st.button("Export as PDF"):
                    pdf = create_employee_report_pdf(reports, employee_filter)
                    st.download_button(
                        label="Download PDF",
                        data=pdf,
                        file_name=f"{employee_filter}_reports_{start_date}_to_{end_date}.pdf",
                        mime="application/pdf"
                    )
        
        # Display reports
        for employee_name, emp_reports in employee_reports.items():
            with st.expander(f"Reports by {employee_name} ({len(emp_reports)})", expanded=True):
                # Group by month/year for better organization
                reports_by_period = {}
                for report in emp_reports:
                    period = report[1].strftime('%B %Y')
                    if period not in reports_by_period:
                        reports_by_period[period] = []
                    reports_by_period[period].append(report)
                
                for period, period_reports in reports_by_period.items():
                    st.markdown(f"##### {period}")
                    for report in period_reports:
                        st.markdown(f'''
                        <div class="report-item">
                            <span style="color: #777;">{report[1].strftime('%A, %d %b %Y')}</span>
                            <p>{report[2]}</p>
                        </div>
                        ''', unsafe_allow_html=True)
            for message in recent_messages
