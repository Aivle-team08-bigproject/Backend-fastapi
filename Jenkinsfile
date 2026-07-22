pipeline {
    agent any

    environment {
        NEXUS_REGISTRY = "10.0.10.20:8082"
        IMAGE_NAME = "backend"
        IMAGE_TAG = "${env.BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    docker.build("${NEXUS_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}")
                }
            }
        }

        stage('Push to Nexus') {
            steps {
                script {
                    docker.withRegistry("http://${NEXUS_REGISTRY}", 'nexus-credentials') {
                        docker.image("${NEXUS_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}").push()
                        docker.image("${NEXUS_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}").push('latest')
                    }
                }
            }
        }
    }

    post {
        success {
            echo "빌드 및 푸시 성공: ${IMAGE_NAME}:${IMAGE_TAG}"
        }
        failure {
            echo "빌드 실패"
        }
    }
}