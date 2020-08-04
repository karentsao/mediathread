Cypress.on('uncaught:exception', (err, runnable) => {
    // returning false here prevents Cypress from
    // failing the test
    return false;
});

describe('Discussion View: Create Discussion', () => {

    beforeEach(() => {
        cy.login('instructor_one', 'test');
        cy.visit('/course/1/');
        cy.get('.card-title a').contains('MAAP Award Reception');
    });

    it('Instructor Creates Discussion', () => {
        cy.visit('/course/1/assignments/');
        cy.get('#cu-privacy-notice-icon').click();

        cy.log('Create a discussion');
        cy.get('button').contains('Add an assignment').should('be.visible');
        cy.get('button').contains('Add an assignment').click()
        cy.get('#discussion-assignment-card').should('be.visible');
        cy.get('#discussion-assignment-card a')
            .contains('Add Assignment').click()

        cy.log('create discussion wizard');
        cy.title().should('eq', 'Mediathread Create Assignment');
        cy.wait(500);
        cy.get('a.nav-link.active').contains('Assignments');
        cy.get('.breadcrumb-item').contains('Back to all assignments');
        cy.get('.page-title').contains('Create Discussion Assignment');
        cy.get('h4:visible').contains('Step 1');
        cy.get('#page2').contains('Get Started');
        cy.get('#page2').click();

        cy.log('Add a title and some text');
        cy.get('h4:visible').contains('Step 2');
        cy.get('input[name="title"]').should('be.visible');
        cy.get('input[name="title"]').click().clear()
            .type('Discussion: Scenario 1');
        cy.getIframeBody().find('p').click()
            .type('A suitable discussion prompt');
        cy.get('#page3').should('be.visible').contains('Next');
        cy.get('#page3:visible').click();

        cy.log('Add a due date');
        cy.get('h4:visible').contains('Step 3');
        cy.get('input[name="due_date"]').should('be.visible');
        cy.get('input[name="due_date"]:visible').click()
        cy.get('.ui-state-default.ui-state-highlight').click();
        cy.get('input[name="due_date"]:visible').invoke('val').should('not.be.empty')
        cy.get('#ui-datepicker-div').should('not.be.visible');
        cy.get('#page4').focus().click();

        cy.log('add publish options & save');
        cy.get('h4:visible').contains('Step 4');
        cy.get('#id_publish_1').should('be.visible');
        cy.get('#id_publish_1').click();
        cy.get('#save-assignment').click();

        cy.title().should('eq', 'Mediathread New Discussion');
    });

    it('should show on assignments page', () => {
        cy.visit('/course/1/assignments');
        cy.get('#cu-privacy-notice-icon').click();
        cy.contains('Discussion: Scenario 1').parent('td').parent('tr').within(() => {
            // all searches are automatically rooted to the found tr element
            cy.get('td').eq(1).contains('Discussion: Scenario 1');
            cy.get('td').eq(2).contains('Shared with Class');
            cy.get('td').eq(3).contains('0 / 3');
            cy.get('td').eq(4).contains('Instructor One');
            cy.get('td').eq(5).contains('Discussion Assignment');
            cy.get('td').eq(7).contains('Delete');
        });
    });
});
